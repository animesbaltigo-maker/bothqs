from __future__ import annotations

import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import FAVORITES_PAGE_SIZE, HISTORY_PAGE_SIZE
from repositories.sqlite_repo import (
    count_favorites,
    count_history,
    get_last_progress,
    list_favorites,
    list_history,
)
from utils.gatekeeper import ensure_channel_membership
from utils.texts import empty_library_text


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


async def send_favorites_page(target, user_id: int, page: int, *, edit: bool = False) -> None:
    total = count_favorites(user_id)
    if total <= 0:
        await _render_panel(
            target,
            text=empty_library_text("Favoritas", "Voce ainda nao favoritou nenhuma HQ."),
            keyboard=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="hq|home")]]),
            edit=edit,
        )
        return

    page_size = max(1, FAVORITES_PAGE_SIZE)
    total_pages = max(1, ((total - 1) // page_size) + 1)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * page_size
    items = list_favorites(user_id, limit=page_size, offset=offset)
    photo = next((item.get("cover_url") for item in items if item.get("cover_url")), "")

    lines = [
        "⭐ <b>Suas favoritas</b>",
        "",
        f"📄 <b>Pagina:</b> {page}/{total_pages}",
        "",
    ]
    for item in items:
        lines.append(
            f"• <b>{html.escape(item.get('title') or 'HQ')}</b>\n"
            f"  <i>{html.escape(item.get('publisher_name') or 'Sem editora')}</i>"
        )

    rows = [
        [InlineKeyboardButton(f"📘 {item.get('title') or 'HQ'}", callback_data=f"hq|open|{item['hq_id']}")]
        for item in items
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"hq|favorites|{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"hq|favorites|{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🏠 Menu", callback_data="hq|home")])

    await _render_panel(target, text="\n".join(lines), keyboard=InlineKeyboardMarkup(rows), photo=photo, edit=edit)


async def send_history_page(target, user_id: int, page: int, *, edit: bool = False) -> None:
    total = count_history(user_id)
    if total <= 0:
        await _render_panel(
            target,
            text=empty_library_text("Historico", "Seu historico ainda esta vazio."),
            keyboard=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="hq|home")]]),
            edit=edit,
        )
        return

    page_size = max(1, HISTORY_PAGE_SIZE)
    total_pages = max(1, ((total - 1) // page_size) + 1)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * page_size
    items = list_history(user_id, limit=page_size, offset=offset)
    photo = next((item.get("cover_url") for item in items if item.get("cover_url")), "")

    lines = [
        "🕘 <b>Historico recente</b>",
        "",
        f"📄 <b>Pagina:</b> {page}/{total_pages}",
        "",
    ]
    rows: list[list[InlineKeyboardButton]] = []

    for item in items:
        title = item.get("title") or "HQ"
        if item.get("chapter_id"):
            label = f"📖 {title} - cap. {item.get('chapter_number') or '?'}"
            page_number = int(item.get("page_number") or 1)
            rows.append(
                [
                    InlineKeyboardButton(
                        label,
                        callback_data=f"hq|reader|{item['chapter_id']}|{item['hq_id']}|{page_number}",
                    )
                ]
            )
            lines.append(
                f"• <b>{html.escape(title)}</b> | cap. <code>{html.escape(item.get('chapter_number') or '?')}</code>"
            )
        else:
            rows.append([InlineKeyboardButton(f"📘 {title}", callback_data=f"hq|open|{item['hq_id']}")])
            lines.append(f"• <b>{html.escape(title)}</b>")

    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"hq|history|{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"hq|history|{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🏠 Menu", callback_data="hq|home")])

    await _render_panel(target, text="\n".join(lines), keyboard=InlineKeyboardMarkup(rows), photo=photo, edit=edit)


async def send_continue_panel(target, context: ContextTypes.DEFAULT_TYPE, user_id: int, *, edit: bool = False) -> None:
    progress = get_last_progress(user_id)
    if not progress:
        await _render_panel(
            target,
            text=empty_library_text("Continuar leitura", "Voce ainda nao tem progresso salvo."),
            keyboard=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="hq|home")]]),
            edit=edit,
        )
        return

    from handlers.hq import send_reader_panel

    await send_reader_panel(
        target,
        context,
        str(progress["chapter_id"]),
        str(progress["hq_id"]),
        int(progress.get("page_number") or 1),
        user_id,
        edit=edit,
    )


async def favoritas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_channel_membership(update, context):
        return
    user = update.effective_user
    if not user:
        return
    await send_favorites_page(update.effective_message, user.id, 1, edit=False)


async def historico(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_channel_membership(update, context):
        return
    user = update.effective_user
    if not user:
        return
    await send_history_page(update.effective_message, user.id, 1, edit=False)


async def continuar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_channel_membership(update, context):
        return
    user = update.effective_user
    if not user:
        return
    await send_continue_panel(update.effective_message, context, user.id, edit=False)

