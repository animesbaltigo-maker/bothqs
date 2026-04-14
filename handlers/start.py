from __future__ import annotations

import asyncio
import re
import time

from telegram import Update
from telegram.ext import ContextTypes

from core.background import fire_and_forget_sync
from handlers.catalog import send_publisher_catalog_page
from handlers.hq import send_home_panel, send_hq_panel, send_reader_panel
from services.metrics import mark_user_seen
from services.referral_db import (
    create_referral,
    register_interaction,
    register_referral_click,
    upsert_user,
)
from services.user_registry import register_user
from utils.gatekeeper import ensure_channel_membership

START_COOLDOWN = 1.0
START_DEEP_LINK_TTL = 8.0

_START_USER_LOCKS: dict[int, asyncio.Lock] = {}
_START_INFLIGHT: dict[str, float] = {}


def _user_lock(user_id: int) -> asyncio.Lock:
    lock = _START_USER_LOCKS.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _START_USER_LOCKS[user_id] = lock
    return lock


def _now() -> float:
    return time.monotonic()


def _deep_link_key(user_id: int, payload: str) -> str:
    return f"{user_id}:{payload}"


def _is_inflight(user_id: int, payload: str) -> bool:
    last = _START_INFLIGHT.get(_deep_link_key(user_id, payload))
    if not last:
        return False
    if _now() - last > START_DEEP_LINK_TTL:
        _START_INFLIGHT.pop(_deep_link_key(user_id, payload), None)
        return False
    return True


def _set_inflight(user_id: int, payload: str) -> None:
    _START_INFLIGHT[_deep_link_key(user_id, payload)] = _now()


def _clear_inflight(user_id: int, payload: str) -> None:
    _START_INFLIGHT.pop(_deep_link_key(user_id, payload), None)


def _start_last_key(user_id: int) -> str:
    return f"start_last:{user_id}"


def _start_last_payload_key(user_id: int) -> str:
    return f"start_last_payload:{user_id}"


def _is_start_cooldown(context: ContextTypes.DEFAULT_TYPE, user_id: int, payload: str) -> bool:
    now = _now()
    last_ts = context.user_data.get(_start_last_key(user_id), 0.0)
    last_payload = context.user_data.get(_start_last_payload_key(user_id), "")
    if payload and payload == last_payload and (now - last_ts) < START_COOLDOWN:
        return True
    context.user_data[_start_last_key(user_id)] = now
    context.user_data[_start_last_payload_key(user_id)] = payload
    return False


def _queue_user_touch(user) -> None:
    def _runner():
        upsert_user(user.id, user.username or "", user.first_name or "")
        register_user(user.id)
        register_interaction(user.id)
        mark_user_seen(user.id, user.username or user.first_name or "")

    fire_and_forget_sync(_runner)


def _extract_hq_id(arg: str) -> str:
    match = re.match(r"^hq_(\d+)$", arg)
    return match.group(1) if match else ""


def _extract_publisher_id(arg: str) -> str:
    match = re.match(r"^pub_(\d+)$", arg)
    return match.group(1) if match else ""


def _extract_reader_payload(arg: str) -> tuple[str, str, int]:
    match = re.match(r"^read_(\d+)_(\d+)(?:_(\d+))?$", arg)
    if not match:
        return "", "", 1
    return match.group(1), match.group(2), int(match.group(3) or 1)


async def _handle_referral(arg: str, user, message) -> None:
    try:
        referrer_id = int(arg.split("_", 1)[1])
    except Exception:
        return

    await asyncio.to_thread(register_referral_click, referrer_id, user.id)
    ok, reason = await asyncio.to_thread(create_referral, referrer_id, user.id)
    if ok:
        text = "Seu convite foi registrado. Continue usando o bot e a indicacao entra em analise."
    elif reason == "self":
        text = "Seu proprio link de convite nao conta."
    elif reason == "already_same":
        text = "Esse convite ja estava associado ao mesmo link."
    else:
        text = "Voce ja entrou no bot por outro convite."
    await message.reply_text(text)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    _queue_user_touch(user)

    if not await ensure_channel_membership(update, context):
        return

    arg = context.args[0].strip() if context.args else ""
    if arg.startswith("ref_"):
        await _handle_referral(arg, user, message)
        arg = ""

    if arg and _is_start_cooldown(context, user.id, arg):
        await message.reply_text("⏳ Aguarde um instante antes de repetir essa acao.")
        return

    if arg and _is_inflight(user.id, arg):
        await message.reply_text("⏳ Essa solicitacao ja esta sendo processada.")
        return

    async with _user_lock(user.id):
        if arg:
            _set_inflight(user.id, arg)

        try:
            hq_id = _extract_hq_id(arg)
            if hq_id:
                await send_hq_panel(message, context, hq_id, user.id, edit=False)
                return

            chapter_id, hint_hq_id, page = _extract_reader_payload(arg)
            if chapter_id:
                await send_reader_panel(message, context, chapter_id, hint_hq_id, page, user.id, edit=False)
                return

            publisher_id = _extract_publisher_id(arg)
            if publisher_id:
                await send_publisher_catalog_page(message, publisher_id, 1, edit=False)
                return

            await send_home_panel(message, context, user.first_name or "leitor", edit=False)
        finally:
            if arg:
                _clear_inflight(user.id, arg)

