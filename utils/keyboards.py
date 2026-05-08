from __future__ import annotations

import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo


BALTIGO_UNIVERSE_WEBAPP_URL = os.getenv(
    "BALTIGO_UNIVERSE_WEBAPP_URL",
    "https://rough-double-remarkable-north.trycloudflare.com/miniapp/bots/index.html",
).strip()


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔎 Buscar HQ", callback_data="hq|search_prompt"),
                InlineKeyboardButton("🔥 Em alta", callback_data="hq|popular|1"),
            ],
            [
                InlineKeyboardButton("🆕 Atualizacoes", callback_data="hq|updates|1"),
                InlineKeyboardButton("📚 Continuar", callback_data="hq|continue"),
            ],
            [
                InlineKeyboardButton("❤️ Favoritas", callback_data="hq|favorites|1"),
                InlineKeyboardButton("🕘 Historico", callback_data="hq|history|1"),
            ],
            [
                InlineKeyboardButton(
                    "⚔️ Universo Baltigo",
                    web_app=WebAppInfo(url=BALTIGO_UNIVERSE_WEBAPP_URL),
                )
            ],
        ]
    )


def back_home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Inicio", callback_data="hq|home")]])


def page_nav_buttons(
    *,
    current_page: int,
    total_pages: int,
    callback_prefix: str,
) -> list[InlineKeyboardButton]:
    buttons: list[InlineKeyboardButton] = []
    if current_page > 1:
        buttons.append(InlineKeyboardButton("⏪", callback_data=f"{callback_prefix}|1"))
        buttons.append(InlineKeyboardButton("⬅️", callback_data=f"{callback_prefix}|{current_page - 1}"))
    if current_page < total_pages:
        buttons.append(InlineKeyboardButton("➡️", callback_data=f"{callback_prefix}|{current_page + 1}"))
        buttons.append(InlineKeyboardButton("⏩", callback_data=f"{callback_prefix}|{total_pages}"))
    return buttons
