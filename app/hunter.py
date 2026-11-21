"""Target discovery, enrichment and scoring. Absolute USD dominates the score."""
import asyncio, time
import httpx
from . import db, prices

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
ADDR = __import__("re").compile(r"^[A-Za-z0-9]{20,110}$")

def _numbers(obj, keys, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and any(s in k.lower() for s in keys):
                out.append(float(v))
            else:
                _numbers(v, keys, out)
    elif isinstance(obj, list):
        for i in obj:
            _numbers(i, keys, out)

def _workers(obj):
    if isinstance(obj, dict):
        w = obj.get("workers")
        if isinstance(w, list):
            return len(w)
        for v in obj.values():
            n = _workers(v)
            if n:
                return n
    if isinstance(obj, list):
        return len(obj)
    return 0

async def discover(client, spec, limit=400):
    if not spec["list"]:
        return 0
    url = "https://" + spec["host"] + spec["list"]
    try:
        r = await client.get(url)
        if r.status_code != 200:
            return 0
        data = r.json()
    except Exception:
        return 0
    addrs = []
    def walk(o):
        if isinstance(o, str) and ADDR.match(o):
            addrs.append(o)
        elif isinstance(o, dict):
            for k, v in o.items():
                if k in ("address", "addr", "miner", "wallet") and isinstance(v, str):
                    addrs.append(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for i in o:
                walk(i)
    walk(data)
    now = int(time.time())
    n = 0
    for a in dict.fromkeys(addrs):
        if len(addrs) > limit and n >= limit:
            break
        db.run("""INSERT INTO targets(pool,coin,chain,address,seen)
                  VALUES(?,?,?,?,?)
                  ON CONFLICT(pool,address) DO UPDATE SET seen=excluded.seen""",
               (spec["id"], spec["coin"], spec["chain"], a, now))
        n += 1
    return n

async def enrich(client, spec, row):
    url = "https://" + spec["host"] + spec["acct"].format(addr=row["address"])
    try:
        r = await client.get(url)
        if r.status_code != 200:
            raise RuntimeError(f"http {r.status_code}")
        data = r.json()
    except Exception as e:
        db.run("UPDATE targets SET err=?, seen=? WHERE id=?", (type(e).__name__, int(time.time()), row["id"]))
        return
    nums = []
    _numbers(data, ("balance", "unpaid", "pending", "confirmed", "immature"), nums)
    bal = max(nums) if nums else 0.0
    thr = 0.0
    raw = str(data)
    import re
    m = re.search(r'"(?:payout_?threshold|min_?payout)"\s*:\s*([0-9.]+)', raw)
    if m:
        thr = float(m.group(1))
    if not thr:
        thr = spec["threshold"] or 0.0
    usd = bal * await prices.usd(spec["coin"])
    prox = min(bal / thr, 1.0) if thr else 0.5
    wk = _workers(data)
    score = usd * (0.4 + prox) * (1 + min(wk, 25) / 25)
    db.run("""UPDATE targets SET balance=?, usd=?, threshold=?, workers=?, score=?,
              seen=?, err=NULL, status='live' WHERE id=?""",
           (bal, usd, thr, wk, score, int(time.time()), row["id"]))

async def cycle():
    limits = httpx.Limits(max_connections=24, max_keepalive_connections=12)
    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=25, limits=limits) as client:
        from .pools import SPECS
        feeds = [s for s in SPECS if s["list"]]
        for spec in feeds:
            try:
                n = await discover(client, spec)
                db.log(f"feed {spec['id']}: {n} addresses")
            except Exception as e:
                db.log(f"feed {spec['id']} err {type(e).__name__}", lvl="warn")
            await asyncio.sleep(0.4)
        rows = db.all("""SELECT * FROM targets WHERE seen > ? ORDER BY score DESC, usd DESC LIMIT 220""",
                      (int(time.time()) - 3600,))
        sem = asyncio.Semaphore(10)
        async def one_row(row):
            spec = next((s for s in SPECS if s["id"] == row["pool"]), None)
            if not spec:
                return
            async with sem:
                await enrich(client, spec, row)
                await asyncio.sleep(0.15)
        await asyncio.gather(*[one_row(r) for r in rows])
    db.run("DELETE FROM targets WHERE seen < ?", (int(time.time()) - 86400 * 3,))