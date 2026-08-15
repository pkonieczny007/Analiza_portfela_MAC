/* Pary transakcji EVM (Base) — ten sam uklad co app.js dla X1, ale:
   - ceny sa juz w USDC (quote pary), wiec nie ma przelacznika jednostek,
   - dochodzi zarzadzanie portfelami EVM (adresy 0x + propozycje z kluczy),
   - endpointy pod /api/eth/pary/*. */
"use strict";

const $ = (sel) => document.querySelector(sel);
let STATE = null;
let TAB = "aktywne";  // aktywne | zakonczone | razem | ukryte
const EPS = 1e-9;
const API = "/api/eth/pary";

function tabFilter(tx) {
  if (TAB === "ukryte") return !!tx.hidden;
  if (tx.hidden) return false;
  if (TAB === "razem") return true;
  if (TAB === "zakonczone") return tx.remaining <= EPS;
  return tx.remaining > EPS;
}

// ---------------------------------------------------------------- helpery

function toast(msg, cls = "") {
  const el = document.createElement("div");
  el.className = "toast " + cls;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 4600);
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

function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

// ilosci w WETH sa male (0.001), ceny w USDC duze (4000) — inne precyzje
function fmtQty(x) {
  if (x == null) return "—";
  return x.toLocaleString("pl-PL", { maximumFractionDigits: 6 });
}
function fmtPrice(x) {
  if (x == null) return "—";
  const digits = Math.abs(x) >= 1 ? 2 : 6;
  return x.toLocaleString("pl-PL", { maximumFractionDigits: digits });
}
function fmtPnl(x) {
  if (x == null) return "—";
  const s = x.toLocaleString("pl-PL", { maximumFractionDigits: 2 });
  return (x > 0 ? "+" : "") + s;
}
function pnlCls(x) { return x == null ? "" : x >= 0 ? "pnl-pos" : "pnl-neg"; }
function fmtDate(ts) {
  return new Date(ts * 1000).toLocaleString("pl-PL", {
    day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}
function shortId(tx) { return `#${tx.id}`; }
function shortAddr(a) { return a ? a.slice(0, 6) + "…" + a.slice(-4) : ""; }

function tsToDateInput(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
function dateInputToTs(val, endOfDay) {
  if (!val) return null;
  const [y, m, d] = val.split("-").map(Number);
  const date = new Date(y, m - 1, d, endOfDay ? 23 : 0, endOfDay ? 59 : 0, endOfDay ? 59 : 0);
  return Math.floor(date.getTime() / 1000);
}

// ---------------------------------------------------------------- render

function renderFilter() {
  const f = STATE.filter || {};
  $("#ep-filter-from").value = tsToDateInput(f.from);
  $("#ep-filter-to").value = tsToDateInput(f.to);
  document.querySelector(".filterbox").classList.toggle("active", !!(f.from || f.to));
}

function render() {
  const s = STATE.stats;
  const price = STATE.price?.price;
  renderFilter();

  $("#ep-price").textContent = price != null
    ? fmtPrice(price) + " " + window.EP_QUOTE : "—";
  const sel = (STATE.wallets || []).filter(w => w.selected);
  $("#ep-wallets-info").textContent = sel.length
    ? "· portfele: " + sel.map(w => w.name).join(", ")
    : "· BRAK zaznaczonych portfeli";

  const kpis = [
    ["Kupione", fmtQty(s.total_buy_qty) + " " + window.EP_BASE],
    ["Sprzedane", fmtQty(s.total_sell_qty) + " " + window.EP_BASE],
    ["Otwarte kupna", fmtQty(s.open_buy_qty)],
    ["Otwarte sprzedaże", fmtQty(s.open_sell_qty)],
    ["Śr. cena kupna", fmtPrice(s.avg_buy_price)],
    ["Śr. cena otwartych", fmtPrice(s.avg_open_price)],
    ["PnL zrealizowany", fmtPnl(s.realized_pnl) + " " + window.EP_QUOTE, pnlCls(s.realized_pnl)],
    ["PnL niezrealizowany", fmtPnl(s.unrealized_pnl) + (s.unrealized_pnl != null ? " " + window.EP_QUOTE : ""), pnlCls(s.unrealized_pnl)],
  ];
  $("#ep-kpis").innerHTML = kpis.map(([k, v, cls]) =>
    `<div class="kpi"><div class="k">${k}</div><div class="v ${cls || ""}">${v}</div></div>`).join("");

  renderWallets();
  renderTabs();
  renderTable("buy");
  renderTable("sell");
  fillMatchSelects();
}

function renderWallets() {
  const wrap = $("#ep-wallets");
  const ws = STATE.wallets || [];
  if (!ws.length) {
    wrap.innerHTML = `<span class="muted">Brak portfeli — wklej adres 0x powyżej albo dodaj z klucza poniżej.</span>`;
  } else {
    wrap.innerHTML = `<table class="tbl"><tbody>` + ws.map(w => `
      <tr>
        <td style="width:30px"><input type="checkbox" ${w.selected ? "checked" : ""}
          title="portfel wchodzi do widoku i syncu"
          onchange="toggleWallet(${w.id}, this.checked)"></td>
        <td><b>${esc(w.name)}</b></td>
        <td class="mono muted" title="${esc(w.address)}">${esc(shortAddr(w.address))}</td>
        <td class="w-actions">
          <button class="iconbtn" title="zmień nazwę" onclick="renameWallet(${w.id}, '${esc(w.name)}')">✎</button>
          <button class="iconbtn" title="usuń (tylko bez transakcji)" onclick="deleteWallet(${w.id})">🗑</button>
        </td>
      </tr>`).join("") + `</tbody></table>`;
  }
  const ks = STATE.key_wallets || [];
  $("#ep-key-suggest").innerHTML = ks.length
    ? `<span class="muted">Klucze z wallet_evm/:</span>` + ks.map(k => `
      <span class="key-chip">
        <b>${esc(k.name)}</b>
        <span class="mono muted">${esc(shortAddr(k.address))}</span>
        <button class="btn small" onclick="addKeyWallet('${esc(k.address)}', '${esc(k.name)}')">+ śledź</button>
      </span>`).join("")
    : "";
}

function renderTabs() {
  const counts = { aktywne: 0, zakonczone: 0, ukryte: 0 };
  for (const t of STATE.txs) {
    if (t.hidden) counts.ukryte++;
    else if (t.remaining <= EPS) counts.zakonczone++;
    else counts.aktywne++;
  }
  counts.razem = counts.aktywne + counts.zakonczone;
  document.querySelectorAll("#ep-tabs .tab").forEach(btn => {
    const key = btn.dataset.tab;
    const label = { aktywne: "Aktywne", zakonczone: "Zakończone",
                    razem: "Aktywne+Zakończone", ukryte: "Ukryte" }[key];
    btn.textContent = `${label} (${counts[key]})`;
    btn.classList.toggle("active", TAB === key);
  });
  $("#ep-unhide-all").style.display = (TAB === "ukryte" && counts.ukryte > 0) ? "" : "none";
}

function progressBar(tx) {
  const pct = tx.qty > 0 ? Math.min(100, tx.matched / tx.qty * 100) : 0;
  const cls = pct >= 99.999 ? "" : "partial";
  return `<div class="progress" title="${fmtQty(tx.matched)} / ${fmtQty(tx.qty)}">
    <div class="fill ${cls}" style="width:${pct}%"></div>
    <span>${fmtQty(tx.matched)} / ${fmtQty(tx.qty)}</span></div>`;
}

function matchLines(tx) {
  if (!tx.matches.length) return "";
  const other = tx.side === "buy" ? "sell" : "buy";
  return tx.matches.map(m => {
    const otherId = tx.side === "buy" ? m.sell_id : m.buy_id;
    const price = tx.side === "buy" ? m.sell_price : m.buy_price;
    return `<div class="match-line">
      <span>↔ ${other === "sell" ? "sprzedaż" : "kupno"} #${otherId}</span>
      <span class="mono">${fmtQty(m.qty)} @ ${fmtPrice(price)}</span>
      <span class="${pnlCls(m.pnl)}">${fmtPnl(m.pnl)} ${window.EP_QUOTE}</span>
      <button class="iconbtn" title="rozłącz parę" onclick="deleteMatch(${m.id})">✕</button>
    </div>`;
  }).join("");
}

function renderTable(side) {
  const tbody = $(side === "buy" ? "#ep-tbl-buys tbody" : "#ep-tbl-sells tbody");
  const txs = STATE.txs.filter(t => t.side === side && tabFilter(t));
  const selCount = (STATE.wallets || []).filter(w => w.selected).length;
  const wname = (id) => {
    const w = (STATE.wallets || []).find(x => x.id === id);
    return w ? w.name : "";
  };
  const rows = [];
  for (const tx of txs) {
    // sygnatura to txHash-logIndex — do basescan idzie sam hash
    const hash = (tx.signature || "").split("-")[0];
    const link = hash
      ? `<a class="muted" href="https://basescan.org/tx/${esc(hash)}" target="_blank" rel="noopener" title="${esc(tx.signature)}">${shortId(tx)}</a>`
      : `<span class="muted">${shortId(tx)}</span>`;
    const hiddenBadge = tx.hidden ? ` <span class="badge-hidden">ukryta</span>` : "";
    const walletBadge = selCount > 1 && tx.wallet_id
      ? ` <span class="muted" style="font-size:10px">[${esc(wname(tx.wallet_id))}]</span>` : "";
    const rowCls = "txrow" + (tx.hidden ? " hidden-row" : "");
    rows.push(`<tr class="${rowCls}">
      <td class="muted">${fmtDate(tx.block_time)} ${link}${walletBadge}${hiddenBadge}</td>
      <td class="r mono">${fmtQty(tx.qty)}</td>
      <td class="r mono">${fmtPrice(tx.price)}</td>
      <td class="r mono">${fmtPrice(tx.quote_amount)}</td>
      <td>${progressBar(tx)}</td>
      <td class="r mono ${pnlCls(tx.realized_pnl)}">${fmtPnl(tx.realized_pnl)}</td>
      <td>${txActions(tx)}</td></tr>`);
    if (tx.matches.length && TAB !== "ukryte") {
      rows.push(`<tr class="matches-row"><td colspan="7">${matchLines(tx)}</td></tr>`);
    }
  }
  const emptyMsg = {
    aktywne: "brak otwartych transakcji — kliknij „Synchronizuj z Base”",
    zakonczone: "nic jeszcze nie zamknięto w całości",
    razem: "brak transakcji — kliknij „Synchronizuj z Base”",
    ukryte: "brak ukrytych transakcji",
  }[TAB];
  tbody.innerHTML = rows.join("") || `<tr><td colspan="7" class="muted">${emptyMsg}</td></tr>`;

  const sum = txs.reduce((a, t) => a + t.qty, 0);
  const open = txs.reduce((a, t) => a + t.remaining, 0);
  const pnl = txs.reduce((a, t) => a + (t.realized_pnl || 0), 0);
  const parts = [`${txs.length} szt.`, `${fmtQty(sum)} ${window.EP_BASE}`];
  if (TAB === "aktywne" || TAB === "razem") parts.push(`otwarte ${fmtQty(open)}`);
  if (TAB === "zakonczone" || TAB === "razem") parts.push(`PnL ${fmtPnl(pnl)} ${window.EP_QUOTE}`);
  $(side === "buy" ? "#ep-buys-summary" : "#ep-sells-summary").textContent = parts.join(" · ");
}

function txActions(tx) {
  if (tx.hidden) {
    return `<button class="iconbtn restore" title="przywróć" onclick="unhideTx(${tx.id})">↩ przywróć</button>`;
  }
  return `<button class="iconbtn" title="ukryj" onclick="hideTx(${tx.id})">👁</button>`;
}

function fillMatchSelects() {
  const buys = STATE.txs.filter(t => t.side === "buy" && t.remaining > EPS && !t.hidden);
  const sells = STATE.txs.filter(t => t.side === "sell" && t.remaining > EPS && !t.hidden);
  $("#ep-match-buy").innerHTML = buys.map(t =>
    `<option value="${t.id}">kupno ${shortId(t)} · ${fmtDate(t.block_time)} · wolne ${fmtQty(t.remaining)} @ ${fmtPrice(t.price)}</option>`
  ).join("") || `<option value="">brak otwartych kupien</option>`;
  $("#ep-match-sell").innerHTML = sells.map(t =>
    `<option value="${t.id}">sprzedaż ${shortId(t)} · ${fmtDate(t.block_time)} · wolne ${fmtQty(t.remaining)} @ ${fmtPrice(t.price)}</option>`
  ).join("") || `<option value="">brak otwartych sprzedaży</option>`;
}

// ---------------------------------------------------------------- akcje

async function load() {
  STATE = await api(API + "/state?hidden=1");
  render();
}

window.toggleWallet = async (id, selected) => {
  await api(`${API}/wallets/${id}`, { method: "PATCH", body: { selected } });
  await load();
};
window.renameWallet = async (id, oldName) => {
  const name = prompt("Nowa nazwa portfela:", oldName);
  if (!name) return;
  await api(`${API}/wallets/${id}`, { method: "PATCH", body: { name } });
  await load();
};
window.deleteWallet = async (id) => {
  if (!confirm("Usunąć portfel z listy śledzonych?")) return;
  try {
    await api(`${API}/wallets/${id}`, { method: "DELETE" });
    await load();
  } catch (e) { toast(e.message, "err"); }
};
window.addKeyWallet = async (address, name) => {
  try {
    await api(API + "/wallets", { method: "POST", body: { address, name } });
    toast("Portfel dodany do śledzenia", "ok");
    await load();
  } catch (e) { toast(e.message, "err"); }
};
window.deleteMatch = async (id) => {
  await api(`${API}/match/${id}`, { method: "DELETE" });
  toast("Para rozłączona", "ok"); await load();
};
window.hideTx = async (id) => {
  if (!confirm("Ukryć transakcję #" + id + "? (znajdziesz ją w zakładce Ukryte)")) return;
  await api(`${API}/tx/${id}`, { method: "PATCH", body: { hidden: true } }); await load();
};
window.unhideTx = async (id) => {
  await api(`${API}/tx/${id}`, { method: "PATCH", body: { hidden: false } });
  toast("Transakcja przywrócona", "ok"); await load();
};

$("#ep-form-wallet").addEventListener("submit", async (e) => {
  e.preventDefault();
  const address = $("#ep-wallet-addr").value.trim();
  const name = $("#ep-wallet-name").value.trim();
  try {
    await api(API + "/wallets", { method: "POST", body: { address, name } });
    toast("Portfel dodany", "ok");
    $("#ep-wallet-addr").value = ""; $("#ep-wallet-name").value = "";
    await load();
  } catch (err) { toast(err.message, "err"); }
});

$("#ep-sync").addEventListener("click", async (e) => {
  const btn = e.target; btn.disabled = true; btn.textContent = "⛓ Pobieram logi…";
  try {
    const r = await api(API + "/sync", { method: "POST" });
    toast(`Sync: ${r.added} nowych, logów ${r.checked}` + (r.errors ? `, błędy: ${r.errors}` : ""),
          r.errors ? "err" : "ok");
    await load();
  } catch (err) { toast("Błąd synchronizacji: " + err.message, "err"); }
  btn.disabled = false; btn.textContent = "⛓ Synchronizuj z Base";
});

$("#ep-automatch").addEventListener("click", async () => {
  const r = await api(API + "/automatch", { method: "POST", body: { strategy: "fifo" } });
  toast(`Utworzono ${r.created} par (FIFO)`, "ok");
  await load();
});

$("#ep-clear-matches").addEventListener("click", async () => {
  const f = STATE?.filter || {};
  const scope = (f.from || f.to)
    ? "dopasowania W AKTYWNYM ZAKRESIE DAT (pary historyczne zostaną)"
    : "WSZYSTKIE dopasowania (także ręczne)";
  if (!confirm(`Usunąć ${scope}?`)) return;
  await api(API + "/matches", { method: "DELETE" });
  await load();
});

async function setFilter(from, to) {
  await api(API + "/filter", { method: "POST", body: { from, to } });
  await load();
  toast(from || to
    ? "Filtr dat zapisany — sync nie zejdzie starzej niż „od”"
    : "Filtr wyłączony — widać wszystko", "ok");
}
$("#ep-filter-apply").addEventListener("click", () =>
  setFilter(dateInputToTs($("#ep-filter-from").value, false), dateInputToTs($("#ep-filter-to").value, true)));
$("#ep-filter-today").addEventListener("click", () => {
  const today = tsToDateInput(Math.floor(Date.now() / 1000));
  setFilter(dateInputToTs(today, false), null);
});
$("#ep-filter-clear").addEventListener("click", () => setFilter(null, null));

document.querySelectorAll("#ep-tabs .tab").forEach(btn => {
  btn.addEventListener("click", () => { TAB = btn.dataset.tab; render(); });
});

$("#ep-unhide-all").addEventListener("click", async () => {
  if (!confirm("Przywrócić wszystkie ukryte transakcje?")) return;
  const r = await api(API + "/tx/unhide_all", { method: "POST", body: {} });
  toast(`Przywrócono ${r.restored} transakcji`, "ok");
  await load();
});

$("#ep-form-match").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api(API + "/match", {
      method: "POST",
      body: {
        buy_id: +$("#ep-match-buy").value,
        sell_id: +$("#ep-match-sell").value,
        qty: parseFloat($("#ep-match-qty").value) || 0,
      },
    });
    toast("Para dodana", "ok");
    $("#ep-match-qty").value = "";
    await load();
  } catch (err) { toast(err.message, "err"); }
});

load().catch(e => toast("Błąd ładowania: " + e.message, "err"));
setInterval(() => load().catch(() => {}), 30000);
