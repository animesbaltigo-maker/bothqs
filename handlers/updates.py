from __future__ import annotations

import html
import json
import logging
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import ADMIN_IDS, BOT_BRAND, BOT_USERNAME, CANAL_POSTAGEM_UPDATES, DATA_DIR, STICKER_DIVISOR
from core.channel_target import ensure_channel_target
from services.admin_settings import get_sticker_divisor
from services.hqnow_client import get_recent_updates_with_chapters

logger = logging.getLogger(__name__)

POSTED_JSON_PATH = Path(DATA_DIR) / "hq_updates_posted.json"


def _is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in ADMIN_IDS


def _load_posted() -> list[str]:
    if not POSTED_JSON_PATH.exists():
        return []
    try:
        return json.loads(POSTED_JSON_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Nao consegui ler o cache de updates postados.", exc_info=True)
        return []


def _save_posted(items: list[str]) -> None:
    POSTED_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    POSTED_JSON_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _deep_link(chapter_id: str, hq_id: str, page: int = 1) -> str:
    return f"https://t.me/{BOT_USERNAME}?start=read_{chapter_id}_{hq_id}_{page}"


def _title_link(hq_id: str) -> str:
    return f"https://t.me/{BOT_USERNAME}?start=hq_{hq_id}"


def _post_key(item: dict) -> str:
    latest = item.get("latest_chapter") or {}
    chapter_id = latest.get("chapter_id") or ""
    if chapter_id:
        return str(chapter_id)
    return str(item.get("hq_id") or "")


def _caption(item: dict) -> str:
    latest = item.get("latest_chapter") or {}
    chapter_number = latest.get("chapter_number") or item.get("updated_chapters") or "Atualizada"
    lines = [
        "🆕 <b>Atualizacao de HQ</b>",
        "",
        f"📚 <b>{html.escape(item.get('title') or 'HQ')}</b>",
        f"🏢 <b>Editora:</b> <i>{html.escape(item.get('publisher_name') or 'Sem editora')}</i>",
        f"📖 <b>Capitulo:</b> <i>{html.escape(str(chapter_number))}</i>",
        "",
        f"✨ <i>Abra no {html.escape(BOT_BRAND)} e continue a leitura sem perder o progresso.</i>",
        "📢 <b>@HQs_Brasil</b>",
    ]
    return "\n".join(lines)


def _keyboard(item: dict) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    latest = item.get("latest_chapter") or {}
    if latest.get("chapter_id"):
        rows.append([InlineKeyboardButton("⚡ Ler agora", url=_deep_link(latest["chapter_id"], item["hq_id"], 1))])
    rows.append([InlineKeyboardButton("📚 Abrir HQ", url=_title_link(item["hq_id"]))])
    return InlineKeyboardMarkup(rows)


async def _send_recent_item(bot, chat_id, item: dict) -> None:
    photo = item.get("cover_url") or ""
    caption = _caption(item)
    keyboard = _keyboard(item)
    if photo:
        try:
            await bot.send_photo(chat_id=chat_id, photo=photo, caption=caption, parse_mode="HTML", reply_markup=keyboard)
            await _send_divider(bot, chat_id)
            return
        except Exception:
            logger.warning("Falha ao enviar capa de update; enviando texto.", exc_info=True)
    await bot.send_message(chat_id=chat_id, text=caption, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)
    await _send_divider(bot, chat_id)


async def _send_divider(bot, destination) -> None:
    sticker_divisor = get_sticker_divisor(STICKER_DIVISOR)
    if not sticker_divisor:
        return
    try:
        await bot.send_sticker(chat_id=destination, sticker=sticker_divisor)
    except Exception:
        logger.warning("Falha ao enviar sticker divisor nos updates.", exc_info=True)


async def _post_recent_items(bot, destination, items: list[dict], posted: list[str]) -> tuple[int, int, list[str]]:
    posted_set = set(posted)
    sent = 0
    failed = 0
    for item in items:
        key = _post_key(item)
        if not key or key in posted_set:
            continue
        try:
            await _send_recent_item(bot, destination, item)
        except Exception:
            failed += 1
            logger.exception("Falha ao postar update da HQ %s", item.get("hq_id"))
            continue
        posted.append(key)
        posted_set.add(key)
        sent += 1
    return sent, failed, posted


async def postupdates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user or not _is_admin(user.id):
        if message:
            await message.reply_text("❌ <b>Voce nao tem permissao para usar esse comando.</b>", parse_mode="HTML")
        return

    status = await message.reply_text("📤 <b>Verificando atualizacoes recentes...</b>", parse_mode="HTML")
    try:
        items = await get_recent_updates_with_chapters(limit=6)
        if not items:
            await status.edit_text("❌ <b>Nao encontrei atualizacoes para postar.</b>", parse_mode="HTML")
            return

        destination = await ensure_channel_target(context.bot, CANAL_POSTAGEM_UPDATES or message.chat_id)
        posted = _load_posted()
        sent, failed, posted = await _post_recent_items(context.bot, destination, items, posted)
        _save_posted(posted[-300:])
        await status.edit_text(
            "✅ <b>Postagem concluida.</b>\n\n"
            f"<b>Atualizacoes enviadas:</b> <code>{sent}</code>\n"
            f"<b>Falhas:</b> <code>{failed}</code>",
            parse_mode="HTML",
        )
    except Exception as error:
        logger.exception("Falha em /postupdates")
        await status.edit_text(
            f"❌ <b>Nao consegui concluir as atualizacoes agora.</b>\n\n<code>{html.escape(str(error))}</code>",
            parse_mode="HTML",
        )


async def auto_post_updates_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not CANAL_POSTAGEM_UPDATES:
        return
    try:
        items = await get_recent_updates_with_chapters(limit=6)
        if not items:
            return
        destination = await ensure_channel_target(context.bot, CANAL_POSTAGEM_UPDATES)
        posted = _load_posted()
        sent, failed, posted = await _post_recent_items(context.bot, destination, items, posted)
        if sent or failed:
            _save_posted(posted[-300:])
    except Exception:
        logger.exception("Falha no auto post de updates.")
