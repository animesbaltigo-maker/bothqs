from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from telegram.error import TimedOut

from config import DISTRIBUTION_TAG, PDF_PROTECT_CONTENT, PDF_QUEUE_LIMIT, PDF_WORKERS_BULK, PDF_WORKERS_SINGLE
from services.epub_service import get_or_build_epub
from services.pdf_service import get_or_build_pdf


logger = logging.getLogger(__name__)


@dataclass
class PdfJob:
    chat_id: int
    chapter_id: str
    chapter_number: str
    title_name: str
    images: list[str]
    caption: str
    is_bulk: bool = False


@dataclass
class EpubJob:
    chat_id: int
    chapter_id: str
    chapter_number: str
    title_name: str
    images: list[str]
    caption: str
    is_bulk: bool = False


_single_workers: list[asyncio.Task] = []
_bulk_workers: list[asyncio.Task] = []
_active_jobs: dict[str, dict] = {}


def _job_key(kind: str, chapter_id: str) -> str:
    return f"{kind}:{chapter_id}"


async def _safe_edit(message, text: str) -> None:
    try:
        await message.edit_text(text, parse_mode="HTML")
    except Exception:
        pass


async def _send_document_safe(bot, chat_id: int, file_path: str, file_name: str, caption: str) -> bool:
    try:
        with open(file_path, "rb") as file:
            await bot.send_document(
                chat_id=chat_id,
                document=file,
                filename=file_name,
                caption=caption,
                parse_mode="HTML",
                protect_content=PDF_PROTECT_CONTENT,
            )
        return True
    except TimedOut:
        try:
            await bot.send_message(chat_id, "O envio demorou mais do que o esperado. Confira se o arquivo ja chegou.")
        except Exception:
            pass
        return True


def _status_text(kind_label: str, title_name: str, chapter_number: str, queue_position: int | None = None) -> str:
    lines = [
        "⏳ <b>Pedido recebido</b>",
        "",
        f"📚 <b>Obra:</b> {title_name}",
        f"📖 <b>Capitulo:</b> {chapter_number}",
        f"📦 <b>Formato:</b> {kind_label}",
    ]
    if queue_position is not None:
        lines.extend(
            [
                f"📍 <b>Posicao aproximada:</b> {queue_position}",
                "",
                "Vou te entregar o arquivo aqui assim que ficar pronto.",
            ]
        )
    else:
        lines.extend(
            [
                "Status: <b>ja esta em processamento</b>",
                "",
                "Voce nao precisa pedir de novo.",
            ]
        )
    lines.extend(
        [
            "",
            "🔒 Compartilhamento protegido",
            f"📢 Distribuicao: <b>{DISTRIBUTION_TAG}</b>",
        ]
    )
    return "\n".join(lines)


async def _progress(entry: dict, title_name: str, chapter_number: str, done: int, total: int) -> None:
    pct = int((done / max(total, 1)) * 100)
    last_pct = int(entry.get("last_pct", -100))
    if pct < 100 and pct - last_pct < 8:
        return

    entry["last_pct"] = pct
    text = (
        f"{entry['icon']} <b>Gerando {entry['kind_label']}</b>\n\n"
        f"📚 <b>Obra:</b> {title_name}\n"
        f"📖 <b>Capitulo:</b> {chapter_number}\n"
        f"⏳ <b>Progresso:</b> {pct}%\n\n"
        "🔒 Compartilhamento protegido\n"
        f"📢 Distribuicao: <b>{DISTRIBUTION_TAG}</b>"
    )
    for message in list(entry["status_messages"]):
        await _safe_edit(message, text)


async def _process_job(app, job: PdfJob | EpubJob) -> None:
    kind = "epub" if isinstance(job, EpubJob) else "pdf"
    entry = _active_jobs.get(_job_key(kind, job.chapter_id))
    if not entry:
        return

    try:

        async def progress_cb(done, total):
            await _progress(entry, job.title_name, job.chapter_number, done, total)

        if isinstance(job, EpubJob):
            file_path, file_name = await get_or_build_epub(
                chapter_id=job.chapter_id,
                chapter_number=job.chapter_number,
                title_name=job.title_name,
                images=job.images,
                progress_cb=progress_cb,
            )
        else:
            file_path, file_name = await get_or_build_pdf(
                chapter_id=job.chapter_id,
                chapter_number=job.chapter_number,
                title_name=job.title_name,
                images=job.images,
                progress_cb=progress_cb,
            )

        for waiter in entry["waiters"]:
            await _send_document_safe(app.bot, waiter["chat_id"], file_path, file_name, waiter["caption"])

        for message in list(entry["status_messages"]):
            await _safe_edit(
                message,
                (
                    f"✅ <b>{entry['kind_label']} pronto</b>\n\n"
                    f"📚 <b>Obra:</b> {job.title_name}\n"
                    f"📖 <b>Capitulo:</b> {job.chapter_number}\n\n"
                    f"📢 <b>{DISTRIBUTION_TAG}</b>"
                ),
            )
    except Exception as error:
        logger.exception("%s generation failed for chapter %s", entry["kind_label"], job.chapter_id)
        for message in list(entry["status_messages"]):
            await _safe_edit(message, f"❌ <b>Falha ao gerar {entry['kind_label']}</b>\n\n<code>{error}</code>")
    finally:
        _active_jobs.pop(_job_key(kind, job.chapter_id), None)


async def _worker(app, queue) -> None:
    while True:
        job = await queue.get()
        if job is None:
            queue.task_done()
            break
        await _process_job(app, job)
        queue.task_done()


async def _enqueue_job(app, job: PdfJob | EpubJob, *, kind: str, kind_label: str, icon: str) -> int:
    single_queue = app.bot_data["single_pdf_queue"]
    bulk_queue = app.bot_data["bulk_pdf_queue"]
    active_key = _job_key(kind, job.chapter_id)

    if active_key in _active_jobs:
        entry = _active_jobs[active_key]
        entry["waiters"].append({"chat_id": job.chat_id, "caption": job.caption})
        status = await app.bot.send_message(
            job.chat_id,
            _status_text(kind_label, job.title_name, job.chapter_number, queue_position=None),
            parse_mode="HTML",
        )
        entry["status_messages"].append(status)
        return single_queue.qsize() + bulk_queue.qsize()

    queue = bulk_queue if job.is_bulk else single_queue
    queue_position = queue.qsize() + 1
    status = await app.bot.send_message(
        job.chat_id,
        _status_text(kind_label, job.title_name, job.chapter_number, queue_position=queue_position),
        parse_mode="HTML",
    )
    _active_jobs[active_key] = {
        "waiters": [{"chat_id": job.chat_id, "caption": job.caption}],
        "status_messages": [status],
        "last_pct": -100,
        "kind_label": kind_label,
        "icon": icon,
    }

    await queue.put(job)
    return single_queue.qsize() + bulk_queue.qsize()


async def enqueue_pdf_job(app, job: PdfJob) -> int:
    return await _enqueue_job(app, job, kind="pdf", kind_label="PDF", icon="📥")


async def enqueue_epub_job(app, job: EpubJob) -> int:
    return await _enqueue_job(app, job, kind="epub", kind_label="EPUB", icon="📦")


async def start_pdf_workers(app) -> None:
    if app.bot_data.get("pdf_workers_started"):
        return

    app.bot_data["single_pdf_queue"] = asyncio.Queue(maxsize=PDF_QUEUE_LIMIT)
    app.bot_data["bulk_pdf_queue"] = asyncio.Queue(maxsize=PDF_QUEUE_LIMIT)

    for _ in range(PDF_WORKERS_SINGLE):
        _single_workers.append(asyncio.create_task(_worker(app, app.bot_data["single_pdf_queue"])))
    for _ in range(PDF_WORKERS_BULK):
        _bulk_workers.append(asyncio.create_task(_worker(app, app.bot_data["bulk_pdf_queue"])))

    app.bot_data["pdf_workers_started"] = True


async def stop_pdf_workers(app) -> None:
    for queue_name, workers in (
        ("single_pdf_queue", _single_workers),
        ("bulk_pdf_queue", _bulk_workers),
    ):
        queue = app.bot_data.get(queue_name)
        if queue is None:
            continue
        for _ in workers:
            await queue.put(None)
        await asyncio.gather(*workers, return_exceptions=True)
        workers.clear()
