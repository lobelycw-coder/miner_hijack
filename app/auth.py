"""Telegram initData HMAC check + signed session tokens."""
import hashlib, hmac, json, os, time, urllib.parse

BOT = os.environ.get("TELEGRAM_BOT_TOKEN", "")
SECRET = os.environ.get("APP_SECRET") or hashlib.sha256(BOT.encode()).hexdigest()
TTL = 86400

def verify(init_data: str):
    if not BOT or not init_data:
        return None
    pairs = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    got = pairs.pop("hash", None)
    if not got:
        return None
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    key = hmac.new(b"WebAppData", BOT.encode(), hashlib.sha256).digest()
    calc = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, got):
        return None
    try:
        if time.time() - int(pairs.get("auth_date", "0")) > TTL:
            return None
        return json.loads(pairs.get("user", "{}")) or None
    except Exception:
        return None

def mint(uid: int) -> str:
    exp = int(time.time()) + TTL * 7
    body = f"{uid}.{exp}"
    return body + "." + hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()

def read(tok: str):
    try:
        uid, exp, sig = tok.split(".")
        body = f"{uid}.{exp}"
        want = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(want, sig) or int(exp) < time.time():
            return None
        return int(uid)
    except Exception:
        return None