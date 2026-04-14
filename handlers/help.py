from telegram import Update
from telegram.ext import ContextTypes

from utils.gatekeeper import ensure_channel_membership
from utils.texts import help_text


async def ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_channel_membership(update, context):
        return
    message = update.effective_message
    if not message:
        return
    await message.reply_text(help_text(), parse_mode="HTML", disable_web_page_preview=True)

