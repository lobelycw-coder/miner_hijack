const tg = window.Telegram && window.Telegram.WebApp;
const $ = (s) => document.querySelector(s);
let token = localStorage.getItem("tok") || "";

function gate(msg) {
  $("#gate").classList.remove("hidden");
  const p = $("#gate p");
  if (p) p.textContent = msg;
}

function h(method, path, body) {
  return fetch(path, {
    method,
    headers: { "Content-Type": "application/json", "X-Session": token },
    body: body ? JSON.stringify(body) : undefined,
  }).then(async (r) => {
    if (r.status === 401) { token = ""; localStorage.removeItem("tok"); throw new Error("auth"); }
    return r.json();
  });
}

async function boot() {
  if (!tg) return gate("telegram-web-app.js did not load. Check your connection.");
  tg.ready(); tg.expand();
  tg.setHeaderColor("#0a0b0d");
  if (!tg.initData) {
    return gate("No initData. Opened as a plain link. Close this and open the app from the bot's menu button.");
  }
  let r;
  try {
    r = await fetch("/api/auth", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ init_data: tg.initData }),
    });
  } catch (e) {
    return gate("Server unreachable. Check the machine is running and /health responds.");
  }
  if (!r.ok) {
    const body = await r.text();
    return gate(`Auth rejected: HTTP ${r.status} ${body.slice(0, 120)} — if 401, TELEGRAM_BOT_TOKEN is unset or belongs to another bot.`);
  }
  const d = await r.json();
  token = d.token; localStorage.setItem("tok", token);

  $("#app").classList.remove("hidden");
  $("#gate").classList.add("hidden");
  $("#who").textContent = (tg.initDataUnsafe.user && tg.initDataUnsafe.user.username) || "anon";
  $("#save").onclick = async () => {
    const v = $("#wallet").value.trim();
    await h("POST", "/api/wallet", { evm: v });
    $("#stat").textContent = "wallet saved";
    paint();
  };
  $("#active").onchange = async (e) => { await h("POST", "/api/settings", { active: e.target.checked }); paint(); };
  $("#minUsd").onchange = async (e) => { await h("POST", "/api/settings", { min_usd: parseFloat(e.target.value) || 0 }); };
  $("#scan").onclick = async () => { $("#stat").textContent = "scanning…"; await h("POST", "/api/scan"); };
  $("#addKey").onclick = async () => {
    const pk = $("#pk").value.trim(); if (!pk) return;
    try { await h("POST", "/api/wallets", { chain: $("#chain").value, pk }); $("#pk").value = ""; paint(); }
    catch (e) { $("#stat").textContent = "bad key"; }
  };
  paint();
  setInterval(paint, 5000);
}

function money(n) { return "$" + (n || 0).toFixed(n >= 100 ? 0 : 2); }

async function paint() {
  let d;
  try { d = await h("GET", "/api/state"); } catch (e) { return; }
  if (d.user) {
    $("#wallet").value = d.user.evm || "";
    $("#active").checked = !!d.user.active;
    $("#minUsd").value = d.user.min_usd;
  }
  $("#targets").innerHTML = (d.targets || []).map((t) => `
    <div class="item">
      <div class="top">
        <span class="pool">${t.pool}</span>
        <span class="val">${money(t.usd)}</span>
      </div>
      <div class="addr">${t.address}</div>
      <div class="top" style="margin-top:6px">
        <span class="st ${t.claim === "ok" ? "ok" : t.claim === "fail" ? "fail" : ""}">${t.claim || "idle"} · ${(t.balance || 0).toFixed(4)} ${t.coin}</span>
        <button class="ghost" data-claim="${t.id}">claim</button>
      </div>
    </div>`).join("");
  document.querySelectorAll("[data-claim]").forEach((b) => {
    b.onclick = async () => {
      b.textContent = "…";
      const r = await h("POST", `/api/targets/${b.dataset.claim}/claim`);
      b.textContent = r.state === "ok" ? "owned" : "fail";
      paint();
    };
  });
  $("#wallets").innerHTML = (d.wallets || []).map((w) =>
    `<div class="item"><span class="pool">${w.chain}</span> <span class="addr">${w.address}</span></div>`).join("");
  $("#sweeps").innerHTML = (d.sweeps || []).map((s) =>
    `<div class="item"><span class="st ${s.state === "sent" ? "ok" : "fail"}">${s.state}</span> ${s.chain} ${(s.amount || 0).toFixed(5)} <span class="addr">${s.address}</span></div>`).join("");
  $("#logs").textContent = (d.logs || []).map((l) => `${new Date(l.ts * 1000).toLocaleTimeString()} [${l.lvl}] ${l.msg}`).join("\n");
}

boot();