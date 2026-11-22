"""Target discovery, enrichment and scoring. Absolute USD dominates the score."""
import asyncio, re, time
import httpx
from . import db, prices
from .pools import SPECS, by_id

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
ADDR = re.compile(r"^[A-Za-z0-9]{20,110}$")
FRESH = 240
ENRICH_CONC = 32
FEED_CONC = 6

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

async def _apply(spec, row, data):
    nums = []
    _numbers(data, ("balance", "unpaid", "pending", "confirmed", "immature"), nums)
    bal = max(nums) if nums else 0.0
    raw = str(data)
    m = re.search(r'"(?:payout_?threshold|min_?payout)"\s*:\s*([0-9.]+)', raw)
    thr = float(m.group(1)) if m else (spec["threshold"] or 0.0)
    usd = bal * await prices.usd(spec["coin"])
    prox = min(bal / thr, 1.0) if thr else 0.5
    wk = _workers(data)
    score = usd * (0.4 + prox) * (1 + min(wk, 25) / 25)
    db.run("""UPDATE targets SET balance=?, usd=?, threshold=?, workers=?, score=?,
              seen=?, err=NULL, status='live' WHERE id=?""",
           (bal, usd, thr, wk, score, int(time.time()), row["id"]))

async def discover(client, spec, limit=400):
    if not spec["list"]:
        return 0
    try:
        r = await client.get("https://" + spec["host"] + spec["list"])
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
    for a in list(dict.fromkeys(addrs))[:limit]:
        db.run("""INSERT INTO targets(pool,coin,chain,address,seen) VALUES(?,?,?,?,?)
                  ON CONFLICT(pool,address) DO UPDATE SET seen=excluded.seen""",
               (spec["id"], spec["coin"], spec["chain"], a, now))
        n += 1
    return n

async def enrich(client, spec, row):
    if time.time() - (row["seen"] or 0) < FRESH and row["err"] is None and row["usd"]:
        return
    try:
        r = await client.get("https://" + spec["host"] + spec["acct"].format(addr=row["address"]))
        if r.status_code != 200:
            raise RuntimeError("http")
        await _apply(spec, row, r.json())
    except Exception as e:
        db.run("UPDATE targets SET err=? WHERE id=?", (type(e).__name__, row["id"]))

async def enrich_bulk(client, spec, rows):
    """One request per pool. Returns the set of row ids it actually filled."""
    done = set()
    if len(rows) < 2:
        return done
    try:
        r = await client.post("https://" + spec["host"] + "/api/accounts",
                              json={"addresses": [x["address"] for x in rows]})
        if r.status_code != 200:
            return done
        data = r.json()
    except Exception:
        return done
    table = {}
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict):
                table[k.lower()] = v
    elif isinstance(data, list):
        for v in data:
            if isinstance(v, dict):
                a = v.get("address") or v.get("addr")
                if isinstance(a, str):
                    table[a.lower()] = v
    for row in rows:
        d = table.get(row["address"].lower())
        if d is not None:
            await _apply(spec, row, d)
            done.add(row["id"])
    return done

async def cycle():
    limits = httpx.Limits(max_connections=48, max_keepalive_connections=24)
    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=20, limits=limits) as client:
        fsem = asyncio.Semaphore(FEED_CONC)
        async def feed(spec):
            async with fsem:
                try:
                    n = await discover(client, spec)
                    db.log(f"feed {spec['id']}: {n}")
                except Exception as e:
                    db.log(f"feed {spec['id']} err {type(e).__name__}", lvl="warn")
        await asyncio.gather(*[feed(s) for s in SPECS if s["list"]])

        now = int(time.time())
        rows = db.all("""SELECT * FROM targets WHERE seen > ?
                         ORDER BY score DESC, usd DESC LIMIT 400""", (now - 7200,))
        groups = {}
        for r in rows:
            groups.setdefault(r["pool"], []).append(r)

        sem = asyncio.Semaphore(ENRICH_CONC)
        async def group(pid, items):
            spec = by_id(pid)
            if not spec:
                return
            done = await enrich_bulk(client, spec, items)
            rest = [r for r in items if r["id"] not in done][:25]
            async def one(r):
                async with sem:
                    await enrich(client, spec, r)
            await asyncio.gather(*[one(r) for r in rest])

        await asyncio.gather(*[group(p, g) for p, g in groups.items()])

    db.run("DELETE FROM targets WHERE seen < ?", (int(time.time()) - 86400 * 3,))