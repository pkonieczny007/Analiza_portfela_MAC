/* Pary transakcji — logika UI */
"use strict";

const $ = (sel) => document.querySelector(sel);
let STATE = null;
let USD = false; // przelaczanie XNT / USDC.x

// ---------------------------------------------------------------- helpery

function toast(msg, cls = "") {
  const el = document.createElement("div");
  el.className = "toast " + cls;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

async function api(path, opts = {}) {
  if (opts.body !== undefined) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(opts.body);
  }
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.status);
  return data;
}

function unitRate() {
  // cena w XNT -> jednostka wyswietlania
  if (USD && STATE?.price?.xnt_usd) return STATE.price.xnt_usd;
  return 1;
}
function unitName() { return USD && STATE?.price?.xnt_usd ? "USDC.x" : "XNT"; }

function fmtQty(x) {
  if (x == null) return "—";
  return x.toLocaleString("pl-PL", { maximumFractionDigits: 2 });
}
function fmtPrice(x) {
  if (x == null) return "—";
  const v = x * unitRate();
  const digits = v >= 1 ? 4 : 7;
  return v.toLocaleString("pl-PL", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}
function fmtPnl(x) {
  if (x == null) return "—";
  const v = x * unitRate();
  const s = v.toLocaleString("pl-PL", { maximumFractionDigits: 4 });
  return (v > 0 ? "+" : "") + s;
}
function pnlCls(x) { return x == null ? "" : x >= 0 ? "pnl-pos" : "pnl-neg"; }
function fmtDate(ts) {
  return new Date(ts * 1000).toLocaleString("pl-PL", {
    day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}
function shortId(tx) {
  return `#${tx.id}${tx.source === "manual" ? "m" : ""}`;
}

// ---------------------------------------------------------------- render

function render() {
  const s = STATE.stats;
  const price = STATE.price.price_xnt;

  $("#price").textContent = fmtPrice(price) + " " + unitName();
  $("#price-usd").textContent = STATE.price.xnt_usd
    ? `1 XNT ≈ ${STATE.price.xnt_usd.toFixed(4)} $` : "(brak kursu USD — klucz x1.ninja)";

  // KPI
  const kpis = [
    ["Kupione", fmtQty(s.total_buy_qty) + " " + STATE.token],
    ["Sprzedane", fmtQty(s.total_sell_qty) + " " + STATE.token],
    ["Otwarte kupna", fmtQty(s.open_buy_qty)],
    ["Otwarte sprzedaże", fmtQty(s.open_sell_qty)],
    ["Śr. cena kupna", fmtPrice(s.avg_buy_price)],
    ["Śr. cena otwartych", fmtPrice(s.avg_open_price)],
    ["PnL zrealizowany", fmtPnl(s.realized_pnl), pnlCls(s.realized_pnl)],
    ["PnL niezrealizowany", fmtPnl(s.unrealized_pnl), pnlCls(s.unrealized_pnl)],
  ];
  $("#kpis").innerHTML = kpis.map(([k, v, cls]) =>
    `<div class="kpi"><div class="k">${k}</div><div class="v ${cls || ""}">${v}</div></div>`).join("");

  renderGroups();
  renderTable("buy");
  renderTable("sell");
  fillMatchSelects();
}

function renderGroups() {
  const wrap = $("#groups");
  const gs = STATE.group_stats;
  if (!gs.length) { wrap.innerHTML = `<span class="muted">Brak grup — dodaj pierwszą.</span>`; return; }
  wrap.innerHTML = gs.map(g => `
    <div class="group-card">
      <div class="g-head">
        <span class="g-name">${esc(g.name)}</span>
        ${g.id != null ? `<span>
          <button class="iconbtn" onclick="renameGroup(${g.id}, '${escAttr(g.name)}')">✎</button>
          <button class="iconbtn" onclick="deleteGroup(${g.id})">🗑</button></span>` : ""}
      </div>
      <div class="g-stats">
        <span>Kupione: <b>${fmtQty(g.buy_qty)}</b></span>
        <span>Otwarte: <b>${fmtQty(g.open_qty)}</b></span>
        <span>Śr. kupna: <b>${fmtPrice(g.avg_buy_price)}</b></span>
        <span>Śr. otwartych: <b>${fmtPrice(g.avg_open_price)}</b></span>
        <span>PnL zreal.: <b class="${pnlCls(g.realized_pnl)}">${fmtPnl(g.realized_pnl)}</b></span>
        <span>PnL niezr.: <b class="${pnlCls(g.unrealized_pnl)}">${fmtPnl(g.unrealized_pnl)}</b></span>
      </div>
    </div>`).join("");
}

function progressBar(tx) {
  const pct = tx.qty > 0 ? Math.min(100, tx.matched / tx.qty * 100) : 0;
  const cls = pct >= 99.999 ? "" : "partial";
  return `<div class="progress" title="${fmtQty(tx.matched)} / ${fmtQty(tx.qty)}">
    <div class="fill ${cls}" style="width:${pct}%"></div>
    <span>${fmtQty(tx.matched)} / ${fmtQty(tx.qty)}</span></div>`;
}

function groupSelect(tx) {
  const opts = [`<option value="">—</option>`].concat(
    STATE.groups.map(g => `<option value="${g.id}" ${tx.group_id === g.id ? "selected" : ""}>${esc(g.name)}</option>`));
  return `<select onchange="setGroup(${tx.id}, this.value)">${opts.join("")}</select>`;
}

function matchLines(tx) {
  if (!tx.matches.length) return "";
  const other = tx.side === "buy" ? "sell" : "buy";
  const lines = tx.matches.map(m => {
    const otherId = tx.side === "buy" ? m.sell_id : m.buy_id;
    const price = tx.side === "buy" ? m.sell_price : m.buy_price;
    const moveTargets = STATE.txs
      .filter(t => t.side === tx.side && t.id !== tx.id && t.remaining > 1e-9)
      .map(t => `<option value="${t.id}">→ ${shortId(t)} (${fmtDate(t.block_time)}, wolne ${fmtQty(t.remaining)})</option>`);
    return `<div class="match-line">
      <span>↔ ${other === "sell" ? "sprzedaż" : "kupno"} #${otherId}</span>
      <span class="mono">${fmtQty(m.qty)} @ ${fmtPrice(price)}</span>
      <span class="${pnlCls(m.pnl)}">${fmtPnl(m.pnl)}</span>
      <select class="mini" onchange="moveMatch(${m.id}, this.value, '${tx.side}')">
        <option value="">przenieś…</option>${moveTargets.join("")}
      </select>
      <button class="iconbtn" title="rozłącz parę" onclick="deleteMatch(${m.id})">✕</button>
    </div>`;
  });
  return lines.join("");
}

function renderTable(side) {
  const tbody = $(side === "buy" ? "#tbl-buys tbody" : "#tbl-sells tbody");
  const txs = STATE.txs.filter(t => t.side === side);
  const rows = [];
  for (const tx of txs) {
    const common = `
      <td class="muted" title="${tx.signature || "ręczna"}">${fmtDate(tx.block_time)} <span class="muted">${shortId(tx)}</span></td>
      <td class="r mono">${fmtQty(tx.qty)}</td>
      <td class="r mono">${fmtPrice(tx.price)}</td>
      <td class="r mono">${fmtPrice(tx.quote_amount)}</td>`;
    if (side === "buy") {
      rows.push(`<tr class="txrow">${common}
        <td>${progressBar(tx)}</td>
        <td class="r mono ${pnlCls(tx.realized_pnl)}">${fmtPnl(tx.realized_pnl)}</td>
        <td>${groupSelect(tx)}</td>
        <td>${txActions(tx)}</td></tr>`);
    } else {
      rows.push(`<tr class="txrow">${common}
        <td>${progressBar(tx)}</td>
        <td>${txActions(tx)}</td></tr>`);
    }
    if (tx.matches.length) {
      const span = side === "buy" ? 8 : 6;
      rows.push(`<tr class="matches-row"><td colspan="${span}">${matchLines(tx)}</td></tr>`);
    }
  }
  tbody.innerHTML = rows.join("") || `<tr><td colspan="8" class="muted">brak transakcji — kliknij „Synchronizuj"</td></tr>`;

  const sum = txs.reduce((a, t) => a + t.qty, 0);
  const open = txs.reduce((a, t) => a + t.remaining, 0);
  $(side === "buy" ? "#buys-summary" : "#sells-summary").textContent =
    `${txs.length} szt. · ${fmtQty(sum)} ${STATE.token} · otwarte ${fmtQty(open)}`;
}

function txActions(tx) {
  const del = tx.source === "manual"
    ? `<button class="iconbtn" title="usuń" onclick="deleteTx(${tx.id})">🗑</button>`
    : `<button class="iconbtn" title="ukryj" onclick="hideTx(${tx.id})">👁</button>`;
  return del;
}

function fillMatchSelects() {
  const buys = STATE.txs.filter(t => t.side === "buy" && t.remaining > 1e-9);
  const sells = STATE.txs.filter(t => t.side === "sell" && t.remaining > 1e-9);
  $("#match-buy").innerHTML = buys.map(t =>
    `<option value="${t.id}">kupno ${shortId(t)} · ${fmtDate(t.block_time)} · wolne ${fmtQty(t.remaining)} @ ${fmtPrice(t.price)}</option>`
  ).join("") || `<option value="">brak otwartych kupien</option>`;
  $("#match-sell").innerHTML = sells.map(t =>
    `<option value="${t.id}">sprzedaż ${shortId(t)} · ${fmtDate(t.block_time)} · wolne ${fmtQty(t.remaining)} @ ${fmtPrice(t.price)}</option>`
  ).join("") || `<option value="">brak otwartych sprzedaży</option>`;
}

function esc(s) { return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function escAttr(s) { return esc(s).replace(/'/g, "\\'"); }

// ---------------------------------------------------------------- akcje

async function load() {
  STATE = await api("/api/state?token=" + window.TOKEN);
  render();
}

window.setGroup = async (txId, val) => {
  await api(`/api/tx/${txId}`, { method: "PATCH", body: { group_id: val ? +val : null } });
  await load();
};
window.deleteMatch = async (id) => {
  await api(`/api/match/${id}`, { method: "DELETE" });
  toast("Para rozłączona", "ok"); await load();
};
window.moveMatch = async (id, target, side) => {
  if (!target) return;
  const body = side === "buy" ? { new_buy_id: +target } : { new_sell_id: +target };
  try {
    await api(`/api/match/${id}/move`, { method: "POST", body });
    toast("Przeniesiono dopasowanie", "ok");
  } catch (e) { toast(e.message, "err"); }
  await load();
};
window.deleteTx = async (id) => {
  if (!confirm("Usunąć ręczną transakcję #" + id + "?")) return;
  await api(`/api/tx/${id}`, { method: "DELETE" }); await load();
};
window.hideTx = async (id) => {
  if (!confirm("Ukryć transakcję #" + id + "? (dopasowania zostaną)")) return;
  await api(`/api/tx/${id}`, { method: "PATCH", body: { hidden: true } }); await load();
};
window.renameGroup = async (id, oldName) => {
  const name = prompt("Nowa nazwa grupy:", oldName);
  if (!name) return;
  await api(`/api/groups/${id}`, { method: "PATCH", body: { name } }); await load();
};
window.deleteGroup = async (id) => {
  if (!confirm("Usunąć grupę? Transakcje zostaną bez grupy.")) return;
  await api(`/api/groups/${id}`, { method: "DELETE" }); await load();
};

$("#btn-sync").addEventListener("click", async (e) => {
  const btn = e.target; btn.disabled = true; btn.textContent = "⛓ Pobieram…";
  try {
    const r = await api("/api/sync", { method: "POST" });
    toast(`Sync: ${r.added} nowych, sprawdzono ${r.checked}` + (r.errors ? `, błędy: ${r.errors}` : ""), "ok");
    await load();
  } catch (err) { toast("Błąd synchronizacji: " + err.message, "err"); }
  btn.disabled = false; btn.textContent = "⛓ Synchronizuj z blockchain";
});

$("#btn-automatch").addEventListener("click", async () => {
  const r = await api("/api/automatch", { method: "POST", body: { strategy: "fifo", token: window.TOKEN } });
  toast(`Utworzono ${r.created} par (FIFO)`, "ok");
  await load();
});

$("#btn-clear-matches").addEventListener("click", async () => {
  if (!confirm("Usunąć WSZYSTKIE dopasowania (także ręczne)?")) return;
  await api("/api/matches?token=" + window.TOKEN, { method: "DELETE" });
  await load();
});

$("#unit-usd").addEventListener("change", (e) => {
  USD = e.target.checked;
  if (USD && !STATE?.price?.xnt_usd) toast("Brak kursu USD — ustaw X1NINJA_API_KEY w .env", "err");
  render();
});

$("#form-group").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = $("#group-name").value.trim();
  if (!name) return;
  await api("/api/groups", { method: "POST", body: { name } });
  $("#group-name").value = "";
  await load();
});

$("#form-match").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/match", {
      method: "POST",
      body: {
        buy_id: +$("#match-buy").value,
        sell_id: +$("#match-sell").value,
        qty: parseFloat($("#match-qty").value) || 0,
      },
    });
    toast("Para dodana", "ok");
    $("#match-qty").value = "";
    await load();
  } catch (err) { toast(err.message, "err"); }
});

$("#form-tx").addEventListener("submit", async (e) => {
  e.preventDefault();
  const dateVal = $("#tx-date").value;
  try {
    await api("/api/tx", {
      method: "POST",
      body: {
        side: $("#tx-side").value,
        qty: parseFloat($("#tx-qty").value),
        price: parseFloat($("#tx-price").value),
        block_time: dateVal ? Math.floor(new Date(dateVal).getTime() / 1000) : null,
        token: window.TOKEN,
      },
    });
    toast("Transakcja dodana", "ok");
    $("#tx-qty").value = ""; $("#tx-price").value = "";
    await load();
  } catch (err) { toast(err.message, "err"); }
});

load().catch(e => toast("Błąd ładowania: " + e.message, "err"));
setInterval(() => load().catch(() => {}), 30000);
