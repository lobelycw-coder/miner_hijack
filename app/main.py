"""FastAPI app. Telegram-only surface, noindex, dashboard API. Also hosts the
aiogram bot (polling) and the fastpath scan loop via lifespan."""

import asyncio
import os
import time
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, commands, db, fastpath, hijack, hunter, scheduler
from .pools import by_id

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None
dp = Dispatcher()
dp.include_router(commands.router)


@asynccontextmanager
async def lifespan(_):
    db.log("boot")
    tasks = [asyncio.create_task(scheduler.loop()), asyncio.create_task(fastpath.loop())]
    polling = None
    if bot is not None:
        await bot.delete_webhook(drop_pending_updates=True)
        polling = asyncio.create_task(dp.start_polling(bot))
    else:
        db.log("TELEGRAM_BOT_TOKEN unset — bot polling disabled", lvl="warn")
    try:
        yield
    finally:
        if polling is not None:
            await dp.stop_polling()
            polling.cancel()
            await bot.session.close()
        for t in tasks:
            t.cancel()


app = FastAPI(title="miner_hijack", docs_url=None, redoc_url=None, lifespan=lifespan)


def uid_of(x_session: str = Header(default="")):
    uid = auth.read(x_session)
    if not uid:
        raise HTTPException(401, "no session")
    return uid


@app.middleware("http")
async def headers(req: Request, call_next):
    r = await call_next(req)
    r.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    r.headers["Referrer-Policy"] = "no-referrer"
    return r


@app.get("/api/diag")
async def diag():
    return {
        "now": int(time.time()),
        "token_set": bool(auth.BOT),
        "token_len": len(auth.BOT),
        "bot_id": auth.BOT.split(":")[0] if ":" in auth.BOT else "",
        "secret_set": bool(os.environ.get("APP_SECRET")),
        "db_ok": True,
        "users": db.one("SELECT COUNT(*) c FROM users")["c"],
        "targets": db.one("SELECT COUNT(*) c FROM targets")["c"],
    }


@app.get("/health")
async def health():
    return {"ok": True, "t": int(time.time())}


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots():
    return "User-agent: *\nDisallow: /\n"


class AuthIn(BaseModel):
    init_data: str


@app.post("/api/auth")
async def auth_in(body: AuthIn):
    u = auth.verify(body.init_data)
    if not u:
        raise HTTPException(401, "bad initData")
    tg = int(u["id"])
    now = int(time.time())
    db.run(
        """INSERT INTO users(tg_id,username,name,created,last_seen) VALUES(?,?,?,?,?)
           ON CONFLICT(tg_id) DO UPDATE SET username=excluded.username,
           name=excluded.name, last_seen=excluded.last_seen""",
        (tg, u.get("username"), u.get("first_name"), now, now),
    )
    return {"token": auth.mint(tg), "tg_id": tg}


class WalletIn(BaseModel):
    evm: str


@app.post("/api/wallet")
async def set_wallet(body: WalletIn, uid: int = Depends(uid_of)):
    w = body.evm.strip()
    if w and not (w.startswith("0x") and len(w) == 42):
        raise HTTPException(400, "bad EVM address")
    db.run("UPDATE users SET evm=? WHERE tg_id=?", (w or None, uid))
    return {"evm": w}


class SetIn(BaseModel):
    active: bool | None