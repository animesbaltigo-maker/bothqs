from __future__ import annotations

import asyncio
import html
import logging
import re
import secrets
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import PROMO_BANNER_URL, SEARCH_LIMIT, SEARCH_PAGE_SIZE, SEARCH_SESSION_TTL_SECONDS
from core.background import fire_and_forget_sync
from services.hqnow_client import get_cached_search_results, search_hqs
from services.metrics import log_event, mark_user_seen
from utils.gatekeeper import ensure_channel_membership
from utils.texts import search_help_text


logger = logging.getLogger(__name__)
RESULTS_PER_PAGE = max(1, SEARCH_PAGE_SIZE)
SEARCH_PROMPT_KEY = "hq_search_prompt"


def _now() -> float:
    return time.monotonic()


def _normalize_query(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _search_session_key(token: str) -> str:
    return f"hq_search_session:{token}"


def _search_last_key(user_id: int) -> str:
    return f"hq_search_last:{user_id}"


def _search_last_query_key(user_id: int) -> str:
    return f"hq_search_last_query:{user_id}"


def store_search_session(context: ContextTypes.DEFAULT_TYPE, query: str, results: list[dict]) -> str:
    token = secrets.token_hex(4)
    context.user_data[_search_session_key(token)] = {
        "query": query,
        "results": results,
        "created_at": time.time(),
    }
    return token


def get_search_session(context: ContextTypes.DEFAULT_TYPE, token: str) -> dict | None:
    payload = context.user_data.get(_search_session_key(token))
    if not isinstance(payload, dict):
        return None
    if time.time() - float(payload.get("created_at", 0.0)) > SEARCH_SESSION_TTL_SECONDS:
        context.user_data.pop(_search_session_key(token), None)
        return None
    return payload


def _is_search_cooldown(context: ContextTypes.DEFAULT_TYPE, user_id: int, query: str) -> bool:
    last_ts = float(context.user_data.get(_search_last_key(user_id), 0.0))
    last_query = str(context.user_data.get(_search_last_query_key(user_id), ""))
    now = _now()
    if query and query == last_query and (now - last_ts) < 1.0:
        return True
    context.user_data[_search_last_key(user_id)] = now
    context.user_data[_search_last_query_key(user_id)] = query
    return False


def _display_line(item: dict) -> str:
    title = _normalize_query(item.get("title") or "HQ")
    publisher = _normalize_query(item.get("publisher_name") or "")
    status = _normalize_query(item.get("status") or "")
    chunks = [title]
    if publisher:
        chunks.append(publisher)
    if status:
        chunks.append(status)
    label = " | ".join(chunks)
    return label[:51].rstrip() + "..." if len(label) > 54 else label


def _build_search_text(query: str, page: int, total: int) -> str:
    total_pages = max(1, ((total - 1) // RESULTS_PER_PAGE) + 1)
    return (
        "🔎 <b>Resultado da busca</b>\n\n"
        f"📚 <b>Pesquisa:</b> {html.escape(query)}\n"
        f"📄 <b>Pagina:</b> {page}/{total_pages}\n"
        f"📦 <b>Resultados:</b> {total}\n\n"
        "Toque em uma HQ para abrir os detalhes."
    )


def build_search_keyboard(results: list[dict], page: int, token: str) -> InlineKeyboardMarkup:
    start = (page - 1) * RESULTS_PER_PAGE
    end = min(start + RESULTS_PER_PAGE, len(results))
    rows: list[list[InlineKeyboardButton]] = []

    for item in results[start:end]:
        rows.append(
            [
                InlineKeyboardButton(
                    f"📘 {_display_line(item)}",
                    callback_data=f"hq|open|{item['hq_id']}",
                )
            ]
        )

    total_pages = max(1, ((len(results) - 1) // RESULTS_PER_PAGE) + 1)
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ Anterior", callback_data=f"hq|search_page|{token}|{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("Proxima ➡️", callback_data=f"hq|search_page|{token}|{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("🏠 Inicio", callback_data="hq|home")])
    return InlineKeyboardMarkup(rows)


def render_search_page(context: ContextTypes.DEFAULT_TYPE, token: str, page: int) -> dict | None:
    session = get_search_session(context, token)
    if not session:
        return None

    results = session.get("results") or []
    if not results:
        return None

    total_pages = max(1, ((len(results) - 1) // RESULTS_PER_PAGE) + 1)
    page = max(1, min(int(page), total_pages))
    photo = next((item.get("cover_url") for item in results if item.get("cover_url")), "") or PROMO_BANNER_URL
    return {
        "photo": photo,
        "text": _build_search_text(session.get("query") or "", page, len(results)),
        "keyboard": build_search_keyboard(results, page, token),
    }


async def edit_search_page(query, rendered: dict) -> None:
    try:
        if rendered.get("photo"):
            await query.edit_message_caption(
                caption=rendered["text"],
                parse_mode="HTML",
                reply_markup=rendered["keyboard"],
            )
            return
    except Exception:
        pass

    try:
        await query.edit_message_text(
            rendered["text"],
            parse_mode="HTML",
            reply_markup=rendered["keyboard"],
            disable_web_page_preview=True,
        )
    except Exception:
        await query.message.reply_text(
            rendered["text"],
            parse_mode="HTML",
            reply_markup=rendered["keyboard"],
            disable_web_page_preview=True,
        )


async def send_search_page(message, rendered: dict) -> None:
    if rendered.get("photo"):
        try:
            await message.reply_photo(
                photo=rendered["photo"],
                caption=rendered["text"],
                parse_mode="HTML",
                reply_markup=rendered["keyboard"],
            )
            return
        except Exception:
            pass

    await message.reply_text(
        rendered["text"],
        parse_mode="HTML",
        reply_markup=rendered["keyboard"],
        disable_web_page_preview=True,
    )


async def _execute_search(update: Update, context: ContextTypes.DEFAULT_TYPE, query_text: str) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not message or not user or not chat:
        return

    if chat.type != "private":
        await message.reply_text(
            "🔒 <b>Essa busca funciona melhor no privado.</b>\n\n"
            "Me chama no PV e envie:\n"
            "<code>/buscar nome da HQ</code>",
            parse_mode="HTML",
        )
        return

    query_text = _normalize_query(query_text)
    if not query_text:
        await message.reply_text(search_help_text(), parse_mode="HTML")
        return

    if len(query_text) < 2:
        await message.reply_text("⚠️ <b>Digite pelo menos 2 caracteres.</b>", parse_mode="HTML")
        return

    if _is_search_cooldown(context, user.id, query_text):
        await message.reply_text("⏳ <b>Aguarde um instante antes de repetir a busca.</b>", parse_mode="HTML")
        return

    fire_and_forget_sync(mark_user_seen, user.id, user.username or user.first_name or "")

    loading = await message.reply_text(
        "🔎 <b>Buscando HQs...</b>\nAguarde um instante.",
        parse_mode="HTML",
    )

    try:
        cached = get_cached_search_results(query_text, limit=SEARCH_LIMIT)
        if cached is not None:
            results = cached
        else:
            results = await asyncio.wait_for(search_hqs(query_text, limit=SEARCH_LIMIT), timeout=10.0)

        fire_and_forget_sync(
            log_event,
            event_type="search",
            user_id=user.id,
            username=user.username or user.first_name or "",
            query_text=query_text,
            result_count=len(results),
        )

        if not results:
            fire_and_forget_sync(
                log_event,
                event_type="search_no_result",
                user_id=user.id,
                username=user.username or user.first_name or "",
                query_text=query_text,
                result_count=0,
            )
            await loading.edit_text(
                "❌ <b>Nenhuma HQ encontrada.</b>\n\nTente outro nome ou variacao do titulo.",
                parse_mode="HTML",
            )
            return

        token = store_search_session(context, query_text, results)
        rendered = render_search_page(context, token, 1)
        try:
            await loading.delete()
        except Exception:
            pass
        if rendered:
            await send_search_page(message, rendered)
        context.user_data[SEARCH_PROMPT_KEY] = False
    except asyncio.TimeoutError:
        await loading.edit_text(
            "⏳ <b>A busca demorou demais.</b>\n\nTente novamente em instantes.",
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("HQ search failed for query %s", query_text)
        await loading.edit_text(
            "❌ <b>Nao consegui concluir a busca agora.</b>\n\nTente novamente em instantes.",
            parse_mode="HTML",
        )


async def buscar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_channel_membership(update, context):
        return
    await _execute_search(update, context, " ".join(context.args or []))


async def search_input_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.user_data.get(SEARCH_PROMPT_KEY):
        return

    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat or chat.type != "private":
        return

    text = _normalize_query(message.text or "")
    if not text or text.startswith("/"):
        return

    await _execute_search(update, context, text)
