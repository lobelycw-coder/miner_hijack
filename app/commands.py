"""
Bot command surface. /start and /app both reply with a web_app button
that launches the Mini App through a signed Telegram launch, so the app
gets initData and clears its gate screen.

Register with `app.include_router(commands.router)` from fastpath.py
(the only one-line edit to an existing file, and it's optional if you
mount it yourself).
"""

from __future__ import annotations

import os

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

# ---- the one string that must match everywhere ----
# Same literal as BotFather /setmenubutton and the HMAC allowlist.
MINI_APP_URL = os.getenv(
    "MINI_APP_URL", "https://miner-hijack.onrender.com/"
)

router = Router(name="commands")


def _launch_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Open Miner Hijack",
                    web_app=WebAppInfo(url=MINI_APP_URL),
                )
            ]
        ]
    )


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Tap below to launch the app. It runs inside Telegram, so your "
        "session is signed automatically — no login.",
        reply_markup=_launch_keyboard(),
    )


@router.message(Command("app"))
async def cmd_app(message: Message) -> None:
    await message.answer(
        "Launch:",
        reply_markup=_launch_keyboard(),
    )