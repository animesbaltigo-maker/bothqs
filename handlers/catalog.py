from __future__ import annotations

import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import POPULAR_LIMIT, PUBLISHERS_PAGE_SIZE, SEARCH_PAGE_SIZE, UPDATES_LIMIT
from services.hqnow_client import (
    get_most_viewed,
    get_publisher_hqs,
    get_recently_updated,
    list_publishers,
)
from utils.gatekeeper import ensure_channel_membership

ITEMS_PER_PAGE = max(1, SEARCH_PAGE_SIZE)


async def _render_panel(target, *, text: str, keyboard: InlineKeyboardMarkup, photo: str = "", edit: bool = False):
    if edit:
        if photo:
            try:
                await target.edit_message_caption(caption=text, parse_mode="HTML", reply_markup=keyboard)
                return
            except Exception:
                pass
        try:
            await target.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)
            return
        except Exception:
            pass

    if photo:
        try:
            await target.reply_photo(photo=photo, caption=text, parse_mode="HTML", reply_markup=keyboard)
            return
        except Exception:
            pass

    await target.reply_text(text, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)


def _slice(items: list[dict], page: int) -> tuple[list[dict], int, int]:
    total_pages = max(1, ((len(items) - 1) // ITEMS_PER_PAGE) + 1)
    page = max(1, min(page, total_pages))
    start = (page - 1) * ITEMS_PER_PAGE
    end = min(start + ITEMS_PER_PAGE, len(items))
    return items[start:end], page, total_pages


async def send_popular_page(target, page: int, *, edit: bool = False) -> None:
    items = await get_most_viewed(limit=max(POPULAR_LIMIT, 40))
    page_items, page, total_pages = _slice(items, page)
    photo = next((item.get("cover_url") for item in page_items if item.get("cover_url")), "")

    lines = [
        "🔥 <b>Mais vistas</b>",
        "",
        f"📄 <b>Pagina:</b> {page}/{total_pages}",
        "",
    ]
    for index, item in enumerate(page_items, start=((page - 1) * ITEMS_PER_PAGE) + 1):
        status = item.get("status") or "N/A"
        lines.append(
            f"{index}. <b>{html.escape(item.get('title') or 'HQ')}</b>\n"
            f"   <i>{html.escape(item.get('publisher_name') or 'Sem editora')}</i> | "
            f"<code>{html.escape(status)}</code>"
        )

    rows = [
        [InlineKeyboardButton(f"📘 {item.get('title') or 'HQ'}", callback_data=f"hq|open|{item['hq_id']}")]
        for item in page_items
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"hq|popular|{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"hq|popular|{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🏠 Menu", callback_data="hq|home")])

    await _render_panel(
        target,
        text="\n".join(lines),
        keyboard=InlineKeyboardMarkup(rows),
        photo=photo,
        edit=edit,
    )


async def send_updates_page(target, page: int, *, edit: bool = False) -> None:
    items = await get_recently_updated(limit=max(UPDATES_LIMIT, 30))
    page_items, page, total_pages = _slice(items, page)
    photo = next((item.get("cover_url") for item in page_items if item.get("cover_url")), "")

    lines = [
        "🆕 <b>Atualizacoes recentes</b>",
        "",
        f"📄 <b>Pagina:</b> {page}/{total_pages}",
        "",
    ]
    for index, item in enumerate(page_items, start=((page - 1) * ITEMS_PER_PAGE) + 1):
        updated = item.get("updated_at") or "N/A"
        if "T" in updated:
            updated = updated.split("T", 1)[0]
        chapter_text = item.get("updated_chapters") or "Atualizada"
        lines.append(
            f"{index}. <b>{html.escape(item.get('title') or 'HQ')}</b>\n"
            f"   <i>{html.escape(chapter_text)}</i> | <code>{html.escape(updated)}</code>"
        )

    rows = [
        [InlineKeyboardButton(f"📘 {item.get('title') or 'HQ'}", callback_data=f"hq|open|{item['hq_id']}")]
        for item in page_items
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"hq|updates|{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"hq|updates|{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🏠 Menu", callback_data="hq|home")])

    await _render_panel(
        target,
        text="\n".join(lines),
        keyboard=InlineKeyboardMarkup(rows),
        photo=photo,
        edit=edit,
    )


async def send_publishers_page(target, page: int, *, edit: bool = False) -> None:
    items = await list_publishers()
    per_page = max(1, PUBLISHERS_PAGE_SIZE)
    total_pages = max(1, ((len(items) - 1) // per_page) + 1)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    end = min(start + per_page, len(items))
    page_items = items[start:end]

    text = (
        "🏢 <b>Editoras</b>\n\n"
        f"📄 <b>Pagina:</b> {page}/{total_pages}\n\n"
        "Escolha uma editora para abrir o catalogo."
    )

    rows = [
        [
            InlineKeyboardButton(
                f"🏢 {item['publisher_name']}",
                callback_data=f"hq|publisher|{item['publisher_id']}|1",
            )
        ]
        for item in page_items
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"hq|publishers|{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"hq|publishers|{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🏠 Menu", callback_data="hq|home")])

    await _render_panel(target, text=text, keyboard=InlineKeyboardMarkup(rows), edit=edit)


async def send_publisher_catalog_page(target, publisher_id: str, page: int, *, edit: bool = False) -> None:
    items = await get_publisher_hqs(publisher_id)
    page_items, page, total_pages = _slice(items, page)
    publisher_name = page_items[0]["publisher_name"] if page_items else f"Editora {publisher_id}"
    photo = next((item.get("cover_url") for item in page_items if item.get("cover_url")), "")

    lines = [
        f"🏢 <b>{html.escape(publisher_name)}</b>",
        "",
        f"📄 <b>Pagina:</b> {page}/{total_pages}",
        "",
    ]
    for item in page_items:
        lines.append(
            f"• <b>{html.escape(item.get('title') or 'HQ')}</b> | "
            f"<code>{html.escape(item.get('status') or 'N/A')}</code>"
        )

    rows = [
        [InlineKeyboardButton(f"📘 {item.get('title') or 'HQ'}", callback_data=f"hq|open|{item['hq_id']}")]
        for item in page_items
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"hq|publisher|{publisher_id}|{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"hq|publisher|{publisher_id}|{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 Editoras", callback_data="hq|publishers|1")])

    await _render_panel(
        target,
        text="\n".join(lines),
        keyboard=InlineKeyboardMarkup(rows),
        photo=photo,
        edit=edit,
    )


async def catalogo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_channel_membership(update, context):
        return
    await send_popular_page(update.effective_message, 1, edit=False)


async def mais_vistas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_channel_membership(update, context):
        return
    await send_popular_page(update.effective_message, 1, edit=False)


async def editoras(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_channel_membership(update, context):
        return
    await send_publishers_page(update.effective_message, 1, edit=False)


async def atualizacoes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_channel_membership(update, context):
        return
    await send_updates_page(update.effective_message, 1, edit=False)

