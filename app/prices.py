"""USD pricing with a 5 minute cache that survives rate limits."""
import time, httpx

IDS = {
    "eth": "ethereum", "bnb": "binancecoin", "matic": "matic-network",
    "etc": "ethereum-classic", "ergo": "ergo", "rvn": "ravencoin",
    "kas": "kaspa", "alph": "alephium", "zeph": "zephyr-protocol",
    "nexa": "nexa", "xmr": "monero", "btc": "bitcoin", "bch": "bitcoin-cash",
    "iron": "iron-fish",
}
# Last known good values. Used only when the feed has never answered.
FALLBACK = {
    "eth": 2600.0, "bnb": 560.0, "matic": 0.40, "etc": 22.0, "ergo": 1.10,
    "rvn": 0.016, "kas": 0.09, "alph": 0.70, "zeph": 0.02, "nexa": 0.000004,
    "xmr": 155.0, "btc": 62000.0, "bch": 330.0, "iron": 0.0005,
}
_CACHE = {"at": 0.0, "px": dict(FALLBACK)}
_TTL = 300
_LAST_FAIL = 0.0

async def usd(coin: str) -> float:
    global _LAST_FAIL
    coin = (coin or "").lower()
    stale = time.time() - _CACHE["at"] > _TTL
    if stale and time.time() - _LAST_FAIL > 30:
        ids = ",".join(sorted(set(IDS.values())))
        fresh = {}
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get("https://api.coingecko.com/api/v3/simple/price",
                                params={"ids": ids, "vs_currencies": "usd"})
            if r.status_code == 200:
                data = r.json()
                fresh = {c: float(data.get(i, {}).get("usd", 0) or 0) for c, i in IDS.items()}
                fresh = {k: v for k, v in fresh.items() if v > 0}
            else:
                _LAST_FAIL = time.time()
        except Exception:
            _LAST_FAIL = time.time()
        if fresh:
            _CACHE["px"].update(fresh)
            _CACHE["at"] = time.time()
        else:
            _CACHE["at"] = time.time() - _TTL + 60
    return _CACHE["px"].get(coin, 0.0)