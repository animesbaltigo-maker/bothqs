from __future__ import annotations

import asyncio
import html
import logging
import time
from urllib.parse import quote

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.ext import ContextTypes

from config import BOT_BRAND, BOT_USERNAME, CHAPTERS_PER_PAGE, PROMO_BANNER_URL
from core.background import fire_and_forget, fire_and_forget_sync, run_sync
from core.pdf_queue import EpubJob, PdfJob, enqueue_epub_job, enqueue_pdf_job
from handlers.catalog import (
    send_popular_page,
    send_publisher_catalog_page,
    send_publishers_page,
    send_updates_page,
)
from handlers.library import send_continue_panel, send_favorites_page, send_history_page
from handlers.search import SEARCH_PROMPT_KEY, edit_search_page, render_search_page
from repositories.sqlite_repo import (
    add_favorite,
    add_history,
    get_progress,
    is_favorite,
    remove_favorite,
    save_progress,
)
from services.hqnow_client import (
    get_cached_chapter_reader_payload,
    get_cached_home_snapshot,
    get_cached_hq_details,
    get_chapter_reader_payload,
    get_home_payload,
    get_hq_details,
)
from services.metrics import get_read_chapter_ids, log_event, mark_chapter_read
from services.telegraph_service import get_cached_chapter_page_url, get_or_create_chapter_page
from utils.keyboards import back_home_keyboard, main_menu_keyboard, page_nav_buttons
from utils.texts import start_text


logger = logging.getLogger(__name__)
CALLBACK_COOLDOWN = 0.7


def _now() -> float:
    return time.monotonic()


def _callback_last_key(user_id: int) -> str:
    return f"hq_callback_last:{user_id}"


def _callback_data_last_key(user_id: int) -> str:
    return f"hq_callback_data_last:{user_id}"


def _is_callback_cooldown(context: ContextTypes.DEFAULT_TYPE, user_id: int, data: str) -> bool:
    last_ts = context.user_data.get(_callback_last_key(user_id), 0.0)
    last_data = context.user_data.get(_callback_data_last_key(user_id), "")
    now = _now()
    if data and last_data == data and (now - last_ts) < CALLBACK_COOLDOWN:
        return True
    context.user_data[_callback_last_key(user_id)] = now
    context.user_data[_callback_data_last_key(user_id)] = data
    return False


async def _safe_answer_query(query, text: str | None = None) -> None:
    try:
        if text is None:
            await query.answer()
        else:
            await query.answer(text, show_alert=False)
    except Exception:
        pass


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _pick_hq_image(hq: dict) -> str:
    return (hq.get("cover_url") or PROMO_BANNER_URL or "").strip()


def _deep_link_hq(hq_id: str | int) -> str:
    return f"https://t.me/{BOT_USERNAME}?start=hq_{hq_id}"


def _share_hq_url(hq: dict) -> str:
    deep_link = _deep_link_hq(hq["hq_id"])
    text = quote(f"Leia {hq.get('title') or 'essa HQ'} comigo no {BOT_BRAND}:")
    return f"https://t.me/share/url?url={quote(deep_link)}&text={text}"


async def _render_panel(target, *, text: str, keyboard: InlineKeyboardMarkup, photo: str = "", edit: bool = False):
    if edit:
        if photo:
            media = InputMediaPhoto(media=photo, caption=text, parse_mode="HTML")
            try:
                await target.edit_message_media(media=media, reply_markup=keyboard)
                return
            except Exception:
                pass
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


def _home_titles(snapshot: dict, key: str, limit: int = 2) -> list[str]:
    titles: list[str] = []
    for item in snapshot.get(key) or []:
        title = str(item.get("title") or "").strip()
        if title:
            titles.append(title)
        if len(titles) >= limit:
            break
    return titles


async def send_home_panel(target, context: ContextTypes.DEFAULT_TYPE, first_name: str, *, edit: bool = False) -> None:
    snapshot = get_cached_home_snapshot(limit=4)
    if not any(snapshot.values()):
        fire_and_forget(get_home_payload(limit=4))

    await _render_panel(
        target,
        text=start_text(
            first_name,
            popular_titles=_home_titles(snapshot, "popular"),
            updated_titles=_home_titles(snapshot, "updates"),
        ),
        keyboard=main_menu_keyboard(),
        photo="",
        edit=edit,
    )


def _hq_text(hq: dict, progress: dict | None, favorite: bool) -> str:
    title = html.escape(hq.get("title") or "HQ")
    publisher = html.escape(hq.get("publisher_name") or "Sem editora")
    status = html.escape(hq.get("status") or "N/A")
    synopsis = html.escape(_truncate(hq.get("synopsis") or "Sem sinopse disponivel no momento.", 420))
    chapters = str(hq.get("chapter_count") or 0)
    views = str(hq.get("impressions_count") or 0)

    lines = [
        f"📚 <b>{title}</b>",
        "",
        f"🏢 <b>Editora:</b> <i>{publisher}</i>",
        f"📌 <b>Status:</b> <i>{status}</i>",
        f"📖 <b>Capitulos:</b> <i>{html.escape(chapters)}</i>",
        f"👀 <b>Views:</b> <i>{html.escape(views)}</i>",
    ]

    if progress:
        lines.append(
            f"⏱ <b>Continuar de:</b> <i>Cap. {html.escape(str(progress.get('chapter_number') or '?'))}"
            f" · Pag. {html.escape(str(progress.get('page_number') or 1))}</i>"
        )

    lines.extend(
        [
            "",
            f"💬 <i>{synopsis}</i>",
            "",
            "❤️ <i>Essa HQ ja esta na sua biblioteca.</i>" if favorite else "✨ <i>Escolha como voce quer abrir essa HQ.</i>",
        ]
    )
    return "\n".join(lines)


def _hq_keyboard(hq: dict, progress: dict | None, favorite: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    first_chapter = hq.get("first_chapter") or {}
    latest_chapter = hq.get("latest_chapter") or {}

    primary_row: list[InlineKeyboardButton] = []
    if progress and progress.get("chapter_id"):
        primary_row.append(
            InlineKeyboardButton(
                "📚 Continuar",
                callback_data=(
                    f"hq|reader|{progress['chapter_id']}|{hq['hq_id']}|"
                    f"{int(progress.get('page_number') or 1)}"
                ),
            )
        )
    if first_chapter.get("chapter_id"):
        primary_row.append(
            InlineKeyboardButton(
                "▶️ Ler do inicio",
                callback_data=f"hq|reader|{first_chapter['chapter_id']}|{hq['hq_id']}|1",
            )
        )
    if primary_row:
        rows.append(primary_row[:2])

    if latest_chapter.get("chapter_id"):
        rows.append(
            [
                InlineKeyboardButton(
                    "🆕 Ultimo capitulo",
                    callback_data=f"hq|reader|{latest_chapter['chapter_id']}|{hq['hq_id']}|1",
                )
            ]
        )

    rows.append([InlineKeyboardButton("📑 Ver capitulos", callback_data=f"hq|chapters|{hq['hq_id']}|1")])
    rows.append(
        [
            InlineKeyboardButton(
                "💔 Remover favorita" if favorite else "❤️ Favoritar",
                callback_data=f"hq|fav|{hq['hq_id']}",
            ),
            InlineKeyboardButton("📤 Compartilhar", url=_share_hq_url(hq)),
        ]
    )
    rows.append([InlineKeyboardButton("🏠 Inicio", callback_data="hq|home")])
    return InlineKeyboardMarkup(rows)


async def send_hq_panel(target, context: ContextTypes.DEFAULT_TYPE, hq_id: str, user_id: int | None, *, edit: bool) -> None:
    hq = get_cached_hq_details(hq_id) or await get_hq_details(hq_id)

    progress = None
    favorite = False
    if user_id:
        progress, favorite = await asyncio.gather(
            run_sync(get_progress, user_id, hq["hq_id"]),
            run_sync(is_favorite, user_id, hq["hq_id"]),
        )
        fire_and_forget_sync(
            add_history,
            user_id,
            event_type="hq_view",
            hq_id=hq["hq_id"],
            title=hq["title"],
            cover_url=hq.get("cover_url") or "",
            site_url=hq.get("site_url") or "",
        )
        fire_and_forget_sync(
            log_event,
            event_type="title_open",
            user_id=user_id,
            title_id=hq["hq_id"],
            title_name=hq["title"],
        )

    await _render_panel(
        target,
        text=_hq_text(hq, progress, favorite),
        keyboard=_hq_keyboard(hq, progress, favorite),
        photo=_pick_hq_image(hq),
        edit=edit,
    )


def _chapter_list_text(hq: dict, page: int, total_items: int) -> str:
    total_pages = max(1, ((total_items - 1) // CHAPTERS_PER_PAGE) + 1)
    return (
        f"📚 <b>{html.escape(hq.get('title') or 'HQ')}</b>\n\n"
        f"📄 <b>Pagina:</b> {page}/{total_pages}\n"
        f"📖 <b>Capitulos:</b> {total_items}\n\n"
        "Toque em um capitulo para abrir a leitura."
    )


def _chapter_list_keyboard(hq: dict, chapters: list[dict], page: int, read_ids: set[str]) -> InlineKeyboardMarkup:
    total_items = len(chapters)
    total_pages = max(1, ((total_items - 1) // CHAPTERS_PER_PAGE) + 1)
    start = (page - 1) * CHAPTERS_PER_PAGE
    end = min(start + CHAPTERS_PER_PAGE, total_items)
    page_items = chapters[start:end]

    rows: list[list[InlineKeyboardButton]] = []
    line: list[InlineKeyboardButton] = []
    for item in page_items:
        chapter_number = str(item.get("chapter_number") or "?")
        label = f"✅ {chapter_number}" if item["chapter_id"] in read_ids else f"📖 {chapter_number}"
        line.append(
            InlineKeyboardButton(
                label,
                callback_data=f"hq|reader|{item['chapter_id']}|{hq['hq_id']}|1",
            )
        )
        if len(line) == 3:
            rows.append(line)
            line = []
    if line:
        rows.append(line)

    nav = page_nav_buttons(current_page=page, total_pages=total_pages, callback_prefix=f"hq|chapters|{hq['hq_id']}")
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("↩️ Voltar para a HQ", callback_data=f"hq|open|{hq['hq_id']}")])
    return InlineKeyboardMarkup(rows)


async def send_chapters_page(target, context: ContextTypes.DEFAULT_TYPE, hq_id: str, page: int, user_id: int | None, *, edit: bool) -> None:
    hq = get_cached_hq_details(hq_id) or await get_hq_details(hq_id)
    read_ids = set(await run_sync(get_read_chapter_ids, user_id, hq["hq_id"])) if user_id else set()
    total_pages = max(1, ((len(hq["chapters"]) - 1) // CHAPTERS_PER_PAGE) + 1)
    page = max(1, min(page, total_pages))

    await _render_panel(
        target,
        text=_chapter_list_text(hq, page, len(hq["chapters"])),
        keyboard=_chapter_list_keyboard(hq, hq["chapters"], page, read_ids),
        photo=_pick_hq_image(hq),
        edit=edit,
    )


def _reader_text(reader: dict, telegraph_url: str = "", notice: str = "") -> str:
    title = html.escape(reader.get("title") or "HQ")
    chapter = html.escape(reader.get("chapter_number") or "?")
    page = int(reader.get("current_page") or 1)
    page_count = int(reader.get("page_count") or 1)

    lines = [
        f"📖 <b>{title}</b>",
        "",
        f"📚 <b>Capitulo:</b> <i>{chapter}</i>",
        f"🖼 <b>Pagina:</b> <i>{page}/{page_count}</i>",
        "💾 <i>Seu progresso esta sendo salvo automaticamente.</i>",
    ]

    if telegraph_url:
        lines.append("⚡ <i>Leitura rapida pronta para abrir.</i>")
    else:
        lines.append("⚡ <i>Toque em Telegraph para preparar a leitura rapida.</i>")

    lines.append("📥 <i>PDF e EPUB chegam aqui no chat quando terminarem.</i>")

    if notice:
        lines.extend(["", notice])

    return "\n".join(lines)


def _reader_keyboard(reader: dict, telegraph_url: str = "") -> InlineKeyboardMarkup:
    current_page = int(reader.get("current_page") or 1)
    page_count = int(reader.get("page_count") or 1)
    chapter_id = reader["chapter_id"]
    title_id = reader["title_id"]

    rows: list[list[InlineKeyboardButton]] = []

    telegraph_row: list[InlineKeyboardButton] = []
    if telegraph_url:
        telegraph_row.append(InlineKeyboardButton("⚡ Abrir no Telegraph", url=telegraph_url))
    else:
        telegraph_row.append(
            InlineKeyboardButton(
                "⚡ Preparar Telegraph",
                callback_data=f"hq|telegraph|{chapter_id}|{title_id}|{current_page}",
            )
        )
    rows.append(telegraph_row)

    rows.append(
        [
            InlineKeyboardButton(
                "📥 Baixar PDF",
                callback_data=f"hq|pdf|{chapter_id}|{title_id}|{current_page}",
            ),
            InlineKeyboardButton(
                "📦 Baixar EPUB",
                callback_data=f"hq|epub|{chapter_id}|{title_id}|{current_page}",
            ),
        ]
    )

    chapter_row: list[InlineKeyboardButton] = []
    if reader.get("previous_chapter"):
        chapter_row.append(
            InlineKeyboardButton(
                "⬅️ Anterior",
                callback_data=f"hq|reader|{reader['previous_chapter']['chapter_id']}|{title_id}|1",
            )
        )
    if reader.get("next_chapter"):
        chapter_row.append(
            InlineKeyboardButton(
                "Proximo ➡️",
                callback_data=f"hq|reader|{reader['next_chapter']['chapter_id']}|{title_id}|1",
            )
        )
    if chapter_row:
        rows.append(chapter_row)

    rows.append([InlineKeyboardButton("📑 Ver capitulos", callback_data=f"hq|chapters|{title_id}|1")])
    rows.append([InlineKeyboardButton("↩️ Voltar para a HQ", callback_data=f"hq|open|{title_id}")])
    return InlineKeyboardMarkup(rows)


async def send_reader_panel(
    target,
    context: ContextTypes.DEFAULT_TYPE,
    chapter_id: str,
    hint_hq_id: str,
    page: int,
    user_id: int | None,
    *,
    edit: bool,
    telegraph_url: str = "",
    notice: str = "",
) -> None:
    reader = get_cached_chapter_reader_payload(chapter_id, page) or await get_chapter_reader_payload(chapter_id, page)
    if not reader.get("title_id") and hint_hq_id:
        reader["title_id"] = hint_hq_id

    if user_id:
        await run_sync(
            save_progress,
            user_id,
            hq_id=reader["title_id"],
            title=reader["title"],
            chapter_id=reader["chapter_id"],
            chapter_number=reader["chapter_number"],
            page_number=reader["current_page"],
            page_count=reader["page_count"],
            reader_url=reader["reader_url"],
            cover_url=reader.get("current_image") or "",
        )
        fire_and_forget_sync(
            add_history,
            user_id,
            event_type="reader",
            hq_id=reader["title_id"],
            title=reader["title"],
            chapter_id=reader["chapter_id"],
            chapter_number=reader["chapter_number"],
            page_number=reader["current_page"],
            cover_url=reader.get("current_image") or "",
            site_url=reader["reader_url"],
        )
        fire_and_forget_sync(
            mark_chapter_read,
            user_id=user_id,
            title_id=reader["title_id"],
            chapter_id=reader["chapter_id"],
            chapter_number=reader["chapter_number"],
            title_name=reader["title"],
            chapter_url=reader["reader_url"],
        )
        fire_and_forget_sync(
            log_event,
            event_type="chapter_open",
            user_id=user_id,
            title_id=reader["title_id"],
            title_name=reader["title"],
            chapter_id=reader["chapter_id"],
            chapter_number=reader["chapter_number"],
        )

    if not telegraph_url:
        telegraph_url = get_cached_chapter_page_url(reader["chapter_id"], reader.get("images") or [])

    await _render_panel(
        target,
        text=_reader_text(reader, telegraph_url=telegraph_url, notice=notice),
        keyboard=_reader_keyboard(reader, telegraph_url=telegraph_url),
        photo=reader.get("current_image") or _pick_hq_image({"cover_url": PROMO_BANNER_URL}),
        edit=edit,
    )


async def _toggle_favorite(query, context: ContextTypes.DEFAULT_TYPE, hq_id: str, user_id: int) -> None:
    hq = get_cached_hq_details(hq_id) or await get_hq_details(hq_id)
    favorite = await run_sync(is_favorite, user_id, hq["hq_id"])
    if favorite:
        await run_sync(remove_favorite, user_id, hq["hq_id"])
        await _safe_answer_query(query, "Favorita removida.")
    else:
        await run_sync(add_favorite, user_id, hq)
        await _safe_answer_query(query, "Favorita adicionada.")
    await send_hq_panel(query, context, hq["hq_id"], user_id, edit=True)


async def _send_telegraph(query, context: ContextTypes.DEFAULT_TYPE, chapter_id: str, hint_hq_id: str, page: int, user_id: int) -> None:
    reader = get_cached_chapter_reader_payload(chapter_id, page) or await get_chapter_reader_payload(chapter_id, page)
    cached_url = get_cached_chapter_page_url(reader["chapter_id"], reader.get("images") or [])
    if cached_url:
        await send_reader_panel(
            query,
            context,
            chapter_id,
            hint_hq_id or reader["title_id"],
            page,
            user_id,
            edit=True,
            telegraph_url=cached_url,
            notice="⚡ <i>Leitura rapida pronta.</i>",
        )
        return

    await send_reader_panel(
        query,
        context,
        chapter_id,
        hint_hq_id or reader["title_id"],
        page,
        user_id,
        edit=True,
        notice="⚡ <i>Estou preparando o Telegraph. Isso pode levar alguns segundos.</i>",
    )

    try:
        url = await get_or_create_chapter_page(
            chapter_id=reader["chapter_id"],
            title=f"{reader['title']} - Capitulo {reader['chapter_number']}",
            images=reader.get("images") or [],
        )
    except Exception as error:
        logger.warning("Telegraph generation failed for %s: %r", chapter_id, error)
        await send_reader_panel(
            query,
            context,
            chapter_id,
            hint_hq_id or reader["title_id"],
            page,
            user_id,
            edit=True,
            notice="❌ <i>Nao consegui preparar a leitura rapida agora. Tente novamente em instantes.</i>",
        )
        return

    await send_reader_panel(
        query,
        context,
        chapter_id,
        hint_hq_id or reader["title_id"],
        page,
        user_id,
        edit=True,
        telegraph_url=url,
        notice="⚡ <i>Leitura rapida pronta.</i>",
    )


async def _enqueue_pdf(query, context: ContextTypes.DEFAULT_TYPE, chapter_id: str, hint_hq_id: str, page: int) -> None:
    reader = get_cached_chapter_reader_payload(chapter_id, page) or await get_chapter_reader_payload(chapter_id, page)
    queue_size = await enqueue_pdf_job(
        context.application,
        PdfJob(
            chat_id=query.message.chat_id,
            chapter_id=reader["chapter_id"],
            chapter_number=reader.get("chapter_number") or "",
            title_name=reader.get("title") or "HQ",
            images=reader.get("images") or [],
            caption=(
                f"📥 <b>{html.escape(reader.get('title') or 'HQ')}</b>\n"
                f"Capitulo <code>{html.escape(reader.get('chapter_number') or '?')}</code>\n\n"
                "🔒 Compartilhamento protegido\n"
                "📢 <b>@HQs_Brasil</b>"
            ),
        ),
    )
    await _safe_answer_query(query, f"PDF entrou na fila. {queue_size} pedido(s) aguardando.")


async def _enqueue_epub(query, context: ContextTypes.DEFAULT_TYPE, chapter_id: str, hint_hq_id: str, page: int) -> None:
    reader = get_cached_chapter_reader_payload(chapter_id, page) or await get_chapter_reader_payload(chapter_id, page)
    queue_size = await enqueue_epub_job(
        context.application,
        EpubJob(
            chat_id=query.message.chat_id,
            chapter_id=reader["chapter_id"],
            chapter_number=reader.get("chapter_number") or "",
            title_name=reader.get("title") or "HQ",
            images=reader.get("images") or [],
            caption=(
                f"📦 <b>{html.escape(reader.get('title') or 'HQ')}</b>\n"
                f"Capitulo <code>{html.escape(reader.get('chapter_number') or '?')}</code>\n\n"
                "🔒 Compartilhamento protegido\n"
                "📢 <b>@HQs_Brasil</b>"
            ),
        ),
    )
    await _safe_answer_query(query, f"EPUB entrou na fila. {queue_size} pedido(s) aguardando.")


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not user or not query.data.startswith("hq|"):
        return

    if _is_callback_cooldown(context, user.id, query.data):
        await _safe_answer_query(query, "Aguarde um instante.")
        return

    parts = query.data.split("|")
    action = parts[1] if len(parts) > 1 else ""

    try:
        if query.data == "hq|noop":
            await _safe_answer_query(query)
            return

        if action == "home":
            context.user_data[SEARCH_PROMPT_KEY] = False
            await _safe_answer_query(query)
            await send_home_panel(query, context, user.first_name or "leitor", edit=True)
            return

        if action == "search_prompt":
            context.user_data[SEARCH_PROMPT_KEY] = True
            await _safe_answer_query(query)
            await _render_panel(
                query,
                text=(
                    "🔎 <b>Buscar HQ</b>\n\n"
                    "Me envie agora o nome da HQ em texto simples.\n\n"
                    "Exemplo: <code>batman</code>"
                ),
                keyboard=back_home_keyboard(),
                edit=True,
            )
            return

        if action == "open" and len(parts) >= 3:
            context.user_data[SEARCH_PROMPT_KEY] = False
            await _safe_answer_query(query)
            await send_hq_panel(query, context, parts[2], user.id, edit=True)
            return

        if action == "chapters" and len(parts) >= 4:
            await _safe_answer_query(query)
            await send_chapters_page(query, context, parts[2], int(parts[3]), user.id, edit=True)
            return

        if action == "reader" and len(parts) >= 5:
            await _safe_answer_query(query)
            await send_reader_panel(query, context, parts[2], parts[3], int(parts[4]), user.id, edit=True)
            return

        if action == "fav" and len(parts) >= 3:
            await _toggle_favorite(query, context, parts[2], user.id)
            return

        if action == "telegraph" and len(parts) >= 5:
            await _safe_answer_query(query, "Preparando leitura rapida...")
            await _send_telegraph(query, context, parts[2], parts[3], int(parts[4]), user.id)
            return

        if action == "pdf" and len(parts) >= 5:
            await _enqueue_pdf(query, context, parts[2], parts[3], int(parts[4]))
            return

        if action == "epub" and len(parts) >= 5:
            await _enqueue_epub(query, context, parts[2], parts[3], int(parts[4]))
            return

        if action == "popular" and len(parts) >= 3:
            await _safe_answer_query(query)
            await send_popular_page(query, int(parts[2]), edit=True)
            return

        if action == "updates" and len(parts) >= 3:
            await _safe_answer_query(query)
            await send_updates_page(query, int(parts[2]), edit=True)
            return

        if action == "publishers" and len(parts) >= 3:
            await _safe_answer_query(query)
            await send_publishers_page(query, int(parts[2]), edit=True)
            return

        if action == "publisher" and len(parts) >= 4:
            await _safe_answer_query(query)
            await send_publisher_catalog_page(query, parts[2], int(parts[3]), edit=True)
            return

        if action == "favorites" and len(parts) >= 3:
            await _safe_answer_query(query)
            await send_favorites_page(query, user.id, int(parts[2]), edit=True)
            return

        if action == "history" and len(parts) >= 3:
            await _safe_answer_query(query)
            await send_history_page(query, user.id, int(parts[2]), edit=True)
            return

        if action == "continue":
            await _safe_answer_query(query)
            await send_continue_panel(query, context, user.id, edit=True)
            return

        if action == "search_page" and len(parts) >= 4:
            await _safe_answer_query(query)
            rendered = render_search_page(context, parts[2], int(parts[3]))
            if not rendered:
                await _safe_answer_query(query, "Essa busca expirou.")
                return
            await edit_search_page(query, rendered)
            return

        await _safe_answer_query(query, "Acao desconhecida.")
    except Exception as error:
        logger.exception("HQ callback failed for %s", query.data)
        await _safe_answer_query(query, "Nao consegui concluir essa acao agora.")
