import asyncio
import logging
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import BOT_TOKEN, LOG_DIR
from core.http_client import close_http_client
from core.pdf_queue import start_pdf_workers, stop_pdf_workers
from handlers.broadcast import (
    broadcast_callbacks,
    broadcast_command,
    broadcast_message_router,
)
from handlers.catalog import atualizacoes, catalogo, editoras, mais_vistas
from handlers.help import ajuda
from handlers.hq import callbacks
from handlers.library import continuar, favoritas, historico
from handlers.metricas import metricas, metricas_limpar
from handlers.posthq import posthq, posttodashqs
from handlers.referral import indicacoes, referral_button
from handlers.referral_admin import auto_referral_check_job, refstats
from handlers.search import buscar, search_input_router
from handlers.start import start
from handlers.sticker_divisor import setdivisor, verdivisor
from handlers.updates import auto_post_updates_job, postupdates
from repositories.sqlite_repo import init_library_db
from services.hqnow_client import warm_catalog_cache
from services.metrics import init_metrics_db
from services.referral_db import init_referral_db

init_metrics_db()
init_referral_db()
init_library_db()

MAX_CONCURRENT_UPDATES = 128
BOT_API_CONNECTION_POOL = 64
BOT_API_POOL_TIMEOUT = 30.0
BOT_API_CONNECT_TIMEOUT = 10.0
BOT_API_READ_TIMEOUT = 25.0
BOT_API_WRITE_TIMEOUT = 25.0


def _configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = Path(LOG_DIR) / "bot.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )


async def _delayed_warm_catalog() -> None:
    await asyncio.sleep(10)
    await warm_catalog_cache()


async def post_init(app: Application) -> None:
    await start_pdf_workers(app)
    app.create_task(_delayed_warm_catalog())


async def post_shutdown(app: Application) -> None:
    await stop_pdf_workers(app)
    await close_http_client()


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.getLogger(__name__).exception("Unhandled bot error", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "❌ <b>Ocorreu um erro ao processar sua solicitacao.</b>",
                parse_mode="HTML",
            )
    except Exception:
        pass


async def warm_catalog_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await warm_catalog_cache()


def _register_jobs(app: Application) -> None:
    if not app.job_queue:
        logging.getLogger(__name__).warning("JobQueue nao disponivel.")
        return

    app.job_queue.run_repeating(auto_post_updates_job, interval=900, first=120, name="hq_auto_updates")
    app.job_queue.run_repeating(auto_referral_check_job, interval=3600, first=60, name="hq_auto_referral")
    app.job_queue.run_repeating(warm_catalog_job, interval=900, first=40, name="hq_warm_cache")


def main() -> None:
    _configure_logging()
    if not BOT_TOKEN:
        raise RuntimeError("Configure BOT_TOKEN nas variaveis de ambiente.")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(MAX_CONCURRENT_UPDATES)
        .connection_pool_size(BOT_API_CONNECTION_POOL)
        .pool_timeout(BOT_API_POOL_TIMEOUT)
        .connect_timeout(BOT_API_CONNECT_TIMEOUT)
        .read_timeout(BOT_API_READ_TIMEOUT)
        .write_timeout(BOT_API_WRITE_TIMEOUT)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("buscar", buscar))
    app.add_handler(CommandHandler("catalogo", catalogo))
    app.add_handler(CommandHandler("maisvistas", mais_vistas))
    app.add_handler(CommandHandler("editoras", editoras))
    app.add_handler(CommandHandler("atualizacoes", atualizacoes))
    app.add_handler(CommandHandler("continuar", continuar))
    app.add_handler(CommandHandler("favoritas", favoritas))
    app.add_handler(CommandHandler("historico", historico))
    app.add_handler(CommandHandler("ajuda", ajuda))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("indicacoes", indicacoes))
    app.add_handler(CommandHandler("refstats", refstats))
    app.add_handler(CommandHandler("metricas", metricas))
    app.add_handler(CommandHandler("metricaslimpar", metricas_limpar))
    app.add_handler(CommandHandler("postupdates", postupdates))
    app.add_handler(CommandHandler("posthq", posthq))
    app.add_handler(CommandHandler("posttodashqs", posttodashqs))
    app.add_handler(CommandHandler("setdivisor", setdivisor))
    app.add_handler(CommandHandler("verdivisor", verdivisor))

    app.add_handler(CallbackQueryHandler(broadcast_callbacks, pattern=r"^bc\|"))
    app.add_handler(CallbackQueryHandler(referral_button, pattern=r"^noop_indicar$"))
    app.add_handler(CallbackQueryHandler(callbacks, pattern=r"^hq\|"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_input_router), group=10)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, broadcast_message_router), group=99)

    _register_jobs(app)
    app.add_error_handler(error_handler)

    logging.getLogger(__name__).info("HQ Baltigo rodando...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
