"""Alert on confirmed swaps and sent sweeps. Never raises, never blocks."""
import asyncio, os, time
import httpx

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
FALLBACK = os.environ.get("ALERT_CHAT_ID", "").strip()
_SEEN = {}
_TTL = 120


async def send(text: str, tg_id: int | None = None) -> None:
    if not TOKEN:
        return
    chat = str(tg_id) if tg_id else FALLBACK
    if not chat:
        return
    key = (chat, text[:120])
    now = time.time()
    if now - _SEEN.get(key, 0) < _TTL:
        return
    _SEEN[key] = now
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            await c.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                         json={"chat_id": chat, "text": text[:3800],
                               "disable_web_page_preview": True})
    except Exception:
        pass


def fire(text: str, tg_id: int | None = None) -> None:
    try:
        asyncio.create_task(send(text, tg_id))
    except RuntimeError:
        pass