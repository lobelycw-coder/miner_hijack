"""
Bot command surface. Complete replacement.

/start and /app reply with a web_app button that launches the Mini App
through a signed Telegram launch so the app receives initData and clears
its gate screen. In chats where a web_app button is rejected (group
without the bot set up for it), it degrades to a url= button.

MINI_APP_URL is REQUIRED — no silent default, because a stale default
launches the wrong origin and re-triggers the gate.
"""

from __future__ import annotations

import logging
import os

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

log = logging.getLogger(__name__)

router = Router(name="commands")

_RAW_URL = os.getenv("MINI_APP_URL", "").strip()

if not _RAW_URL:
    raise RuntimeError(
        "MINI_APP_URL is not set. Set it on Render to the exact deployed "
        "origin, e.g. https://miner-hijack.onrender.com/"
    )
if not _RAW_URL.startswith("https://"):
    raise RuntimeError(f"MINI_APP_URL must be https, got {_RAW_URL!r}")
if not _RAW_URL.endswith("/"):
    _RAW_URL += "/"

MINI_APP_URL: str = _RAW_URL


def _keyboard(message: Message) -> InlineKeyboardMarkup:
    # web_app buttons are only valid in private chats; elsewhere use url=
    if message.chat.type == "private":
        btn = InlineKeyboardButton(
            text="🚀 Open Miner Hijack",
            web_app=WebAppInfo(url=MINI_APP_URL),
        )
    else:
        btn = InlineKeyboardButton(
            text="🚀 Open Miner Hijack",
            url=MINI_APP_URL,
        )
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Tap below to launch the app. It runs inside Telegram, so your "
        "session is signed automatically — no login.",
        reply_markup=_keyboard(message),
    )


@router.message(Command("app"))
async def cmd_app(message: Message) -> None:
    await message.answer("Launch:", reply_markup=_keyboard(message))


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "/start — open the app\n"
        "/app — launch directly\n"
        "/help — this message\n\n"
        "Operator commands are admin-gated.",
    )