from __future__ import annotations

import asyncio
import html
import json
import unicodedata
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import ADMIN_IDS, BOT_USERNAME, CANAL_POSTAGEM_HQS, DATA_DIR, STICKER_DIVISOR
from core.channel_target import ensure_channel_target
from services.admin_settings import get_sticker_divisor
from services.hqnow_client import get_cached_hq_details, get_hq_details, get_series_catalog, search_hqs

POSTED_JSON_PATH = Path(DATA_DIR) / "hqs_postadas.json"
BULK_POST_DELAY_SECONDS = 30.0
GLOBAL_BULK_RUNNING_KEY = "hq_bulk_post_running"
GLOBAL_BULK_TASK_KEY = "hq_bulk_post_task"


def _truncate_text(text: str, limit: int = 320) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in ADMIN_IDS


def _normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", (value or "").strip().lower())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(value.split())


def _pick_best_candidate(query: str, results: list[dict]) -> dict | None:
    if not results:
        return None

    normalized_query = _normalize_text(query)

    def _score(item: dict) -> tuple[int, int]:
        display_title = _normalize_text(item.get("display_title") or item.get("title") or "")
        if not display_title:
            return (-1, 0)
        if display_title == normalized_query:
            return (500, -len(display_title))
        if display_title.startswith(normalized_query):
            return (400, -len(display_title))
        if normalized_query in display_title:
            return (300, -len(display_title))
        overlap = len(set(normalized_query.split()) & set(display_title.split()))
        return (100 + overlap, -len(display_title))

    return max(results, key=_score)


def _build_caption(hq: dict) -> str:
    title = html.escape((hq.get("title") or "Sem titulo").upper())
    publisher = html.escape(hq.get("publisher_name") or "Sem editora")
    status = html.escape(str(hq.get("status") or "N/A"))
    chapters = html.escape(str(hq.get("chapter_count") or "?"))
    description = html.escape(_truncate_text(hq.get("synopsis") or "", 320))

    return (
        f"📚 <b>{title}</b>\n\n"
        f"<b>Editora:</b> <i>{publisher}</i>\n"
        f"<b>Status:</b> <i>{status}</i>\n"
        f"<b>Capitulos:</b> <i>{chapters}</i>\n\n"
        f"💬 {description or 'Sem descricao disponivel.'}"
    )


def _build_keyboard(hq: dict) -> InlineKeyboardMarkup:
    hq_id = hq.get("hq_id") or ""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Ler HQ", url=f"https://t.me/{BOT_USERNAME}?start=hq_{hq_id}")]]
    )


def _load_posted() -> list[str]:
    if not POSTED_JSON_PATH.exists():
        return []
    try:
        return json.loads(POSTED_JSON_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_posted(items: list[str]) -> None:
    POSTED_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    POSTED_JSON_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _bulk_running(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(context.application.bot_data.get(GLOBAL_BULK_RUNNING_KEY, False))


def _set_bulk_running(context: ContextTypes.DEFAULT_TYPE, value: bool) -> None:
    context.application.bot_data[GLOBAL_BULK_RUNNING_KEY] = value


def _set_bulk_task(context: ContextTypes.DEFAULT_TYPE, task) -> None:
    context.application.bot_data[GLOBAL_BULK_TASK_KEY] = task


async def _safe_edit(message, text: str) -> None:
    try:
        await message.edit_text(text, parse_mode="HTML")
    except Exception:
        pass


async def _send_divider(bot, destination) -> None:
    sticker_divisor = get_sticker_divisor(STICKER_DIVISOR)
    if not sticker_divisor:
        return
    try:
        await bot.send_sticker(chat_id=destination, sticker=sticker_divisor)
    except Exception as error:
        print("ERRO STICKER DIVISOR HQ:", repr(error), sticker_divisor)


async def _resolve_hq_payload(hq_ref: dict) -> dict | None:
    hq_id = str(hq_ref.get("hq_id") or "").strip()
    if not hq_id:
        return None

    bundle = get_cached_hq_details(hq_id)
    if bundle is None:
        bundle = await asyncio.wait_for(get_hq_details(hq_id), timeout=18.0)
    return dict(bundle)


async def _send_hq_post(bot, destination, hq: dict) -> None:
    photo = hq.get("cover_url") or None
    caption = _build_caption(hq)
    keyboard = _build_keyboard(hq)

    if photo:
        try:
            await bot.send_photo(
                chat_id=destination,
                photo=photo,
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception as photo_error:
            print("ERRO POSTHQ FOTO:", repr(photo_error))
            await bot.send_message(
                chat_id=destination,
                text=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
    else:
        await bot.send_message(
            chat_id=destination,
            text=caption,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )

    await _send_divider(bot, destination)


async def posthq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else None
    message = update.effective_message

    if not message:
        return

    if not _is_admin(user_id):
        await message.reply_text("Voce nao tem permissao para usar este comando.", parse_mode="HTML")
        return

    if not context.args:
        await message.reply_text(
            "<b>Faltou o nome da HQ.</b>\n\n"
            "Use assim:\n"
            "<code>/posthq nome da obra</code>",
            parse_mode="HTML",
        )
        return

    query = " ".join(context.args).strip()
    status_message = await message.reply_text(
        "<b>Montando postagem...</b>\nAguarde um instante.",
        parse_mode="HTML",
    )

    try:
        results = await search_hqs(query, limit=8)
        if not results:
            await status_message.edit_text("<b>Nao encontrei essa HQ.</b>", parse_mode="HTML")
            return

        search_item = _pick_best_candidate(query, results)
        if not search_item or not search_item.get("hq_id"):
            await status_message.edit_text("<b>Nao consegui identificar a HQ certa.</b>", parse_mode="HTML")
            return

        hq = await _resolve_hq_payload(search_item)
        if not hq:
            await status_message.edit_text("<b>Nao consegui montar os dados dessa HQ.</b>", parse_mode="HTML")
            return

        destination = await ensure_channel_target(context.bot, CANAL_POSTAGEM_HQS or message.chat_id)
        await _send_hq_post(context.bot, destination, hq)

        await status_message.edit_text(
            f"<b>Postagem enviada com sucesso.</b>\n\n<code>{html.escape(hq.get('title') or query)}</code>",
            parse_mode="HTML",
        )
    except Exception as error:
        print("ERRO POSTHQ:", repr(error))
        await status_message.edit_text(
            f"<b>Nao consegui postar essa HQ.</b>\n\n{html.escape(str(error) or 'Tente novamente em instantes.')}",
            parse_mode="HTML",
        )


async def _run_bulk_post_hqs(
    context: ContextTypes.DEFAULT_TYPE,
    admin_chat_id: int,
    reply_to_message_id: int | None,
):
    _set_bulk_running(context, True)
    try:
        destination = await ensure_channel_target(context.bot, CANAL_POSTAGEM_HQS or admin_chat_id)
        catalog = await get_series_catalog()
        posted = _load_posted()
        posted_set = set(posted)
        pending = [item for item in catalog if str(item.get("hq_id") or "").strip() and str(item.get("hq_id")) not in posted_set]

        if not pending:
            await context.bot.send_message(
                chat_id=admin_chat_id,
                text="Nenhuma HQ pendente para postar agora.",
                reply_to_message_id=reply_to_message_id,
            )
            return

        status_message = await context.bot.send_message(
            chat_id=admin_chat_id,
            text=(
                "<b>Postagem em lote iniciada.</b>\n\n"
                f"<b>Total pendente:</b> <code>{len(pending)}</code>\n"
                f"<b>Intervalo:</b> <code>{int(BULK_POST_DELAY_SECONDS)}s</code>"
            ),
            parse_mode="HTML",
            reply_to_message_id=reply_to_message_id,
        )

        sent = 0
        failed = 0
        total = len(pending)

        for index, item in enumerate(pending, start=1):
            hq_id = str(item.get("hq_id") or "").strip()
            title = str(item.get("title") or "HQ").strip()

            try:
                hq = await _resolve_hq_payload(item)
                if not hq:
                    raise RuntimeError("Nao consegui montar a HQ.")
                await _send_hq_post(context.bot, destination, hq)
                posted.append(hq_id)
                posted_set.add(hq_id)
                _save_posted(posted[-5000:])
                sent += 1
            except Exception as error:
                failed += 1
                print("ERRO POSTHQ BULK:", repr(error), hq_id, title)

            await _safe_edit(
                status_message,
                (
                    "<b>Postagem em lote em andamento.</b>\n\n"
                    f"<b>Enviadas:</b> <code>{sent}</code>\n"
                    f"<b>Falhas:</b> <code>{failed}</code>\n"
                    f"<b>Processadas:</b> <code>{index}/{total}</code>\n"
                    f"<b>Atual:</b> <code>{html.escape(title)}</code>"
                ),
            )

            if index < total:
                await asyncio.sleep(BULK_POST_DELAY_SECONDS)

        await _safe_edit(
            status_message,
            (
                "<b>Postagem em lote finalizada.</b>\n\n"
                f"<b>Enviadas:</b> <code>{sent}</code>\n"
                f"<b>Falhas:</b> <code>{failed}</code>\n"
                f"<b>Total analisado:</b> <code>{total}</code>"
            ),
        )
    finally:
        _set_bulk_running(context, False)
        context.application.bot_data.pop(GLOBAL_BULK_TASK_KEY, None)


async def posttodashqs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else None
    message = update.effective_message

    if not message:
        return

    if not _is_admin(user_id):
        await message.reply_text("Voce nao tem permissao para usar este comando.", parse_mode="HTML")
        return

    if _bulk_running(context):
        await message.reply_text("<b>Ja existe uma postagem em lote rodando.</b>", parse_mode="HTML")
        return

    task = context.application.create_task(
        _run_bulk_post_hqs(
            context=context,
            admin_chat_id=message.chat_id,
            reply_to_message_id=message.message_id,
        )
    )
    _set_bulk_task(context, task)

    await message.reply_text(
        "<b>Fila de postagem em lote iniciada.</b>\n\n"
        "Vou enviar uma HQ, depois o sticker divisor, e seguir nesse ritmo com 30 segundos entre uma postagem e outra.",
        parse_mode="HTML",
    )
