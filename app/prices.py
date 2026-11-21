"""USD pricing with a 5 minute cache."""
import time, httpx

IDS = {
    "eth": "ethereum", "bnb": "binancecoin", "matic": "matic-network",
    "etc": "ethereum-classic", "ergo": "ergo", "rvn": "ravencoin",
    "kas": "kaspa", "alph": "alephium", "zeph": "zephyr-protocol",
    "nexa": "nexa", "xmr": "monero", "btc": "bitcoin", "bch": "bitcoin-cash",
    "iron": "iron-fish",
}
_CACHE = {"at": 0.0, "px": {}}

async def usd(coin: str) -> float:
    coin = (coin or "").lower()
    if time.time() - _CACHE["at"] > 300 or coin not in _CACHE["px"]:
        ids = ",".join(sorted(set(IDS.values())))
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get("https://api.coingecko.com/api/v3/simple/price",
                                params={"ids": ids, "vs_currencies": "usd"})
                data = r.json() if r.status_code == 200 else {}
        except Exception:
            data = {}
        _CACHE["px"] = {c: float(data.get(i, {}).get("usd", 0) or 0) for c, i in IDS.items()}
        _CACHE["at"] = time.time()
    return _CACHE["px"].get(coin, 0.0)