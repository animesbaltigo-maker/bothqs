from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_IDS
from services.admin_settings import get_sticker_divisor, set_sticker_divisor


def _is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in ADMIN_IDS


async def setdivisor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user

    if not message or not user or not _is_admin(user.id):
        if message:
            await message.reply_text("Voce nao tem permissao para usar esse comando.", parse_mode="HTML")
        return

    replied = message.reply_to_message
    sticker = getattr(replied, "sticker", None)
    if not sticker:
        await message.reply_text(
            "<b>Responda a um sticker com este comando.</b>\n\n"
            "Exemplo:\n"
            "1. envie o sticker aqui no chat\n"
            "2. responda a ele com <code>/setdivisor</code>",
            parse_mode="HTML",
        )
        return

    file_id = set_sticker_divisor(sticker.file_id)
    await message.reply_text(
        "<b>Sticker divisor salvo para este bot.</b>\n\n"
        f"<code>{file_id}</code>",
        parse_mode="HTML",
    )


async def verdivisor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user

    if not message or not user or not _is_admin(user.id):
        if message:
            await message.reply_text("Voce nao tem permissao para usar esse comando.", parse_mode="HTML")
        return

    sticker = get_sticker_divisor()
    if not sticker:
        await message.reply_text("<b>Nenhum sticker divisor salvo ainda.</b>", parse_mode="HTML")
        return

    await message.reply_text(
        "<b>Sticker divisor atual:</b>\n\n"
        f"<code>{sticker}</code>",
        parse_mode="HTML",
    )
