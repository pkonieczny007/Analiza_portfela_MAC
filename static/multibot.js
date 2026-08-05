/* MultiBOT — zlecenia dzielone na transze.
   Rozklad per transza: wielkosc pozycji, odstepy czasu (mix czasu),
   przesuniecie ceny w % — suwaki + Rowno/Mix + blokada grupy. */
"use strict";

const $ = (s) => document.querySelector(s);
let SIDE = "buy";
let TRIGGER = "time_price";
let SETTINGS = null;
let SHOW_HIDDEN = false;

/* Rozklad transz: size 1..100 (wagi ilosci), time 1..100 (dlugosci odstepow),
   price -90..90 (offset % zakresu cenowego per transza). */
const W = { size: [], time: [], price: [] };
const LOCK = { size: false, time: false, price: false };

const GROUPS = ["size", "time", "price"];
const SKEW_SIGN = { size: 1, time: -1, price: 1 };  // time: prawo = gesciej na koncu

function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function fmt(x, d = 4) { return x == null ? "—" : x.toLocaleString("pl-PL", { maximumFractionDigits: d }); }
function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleString("pl-PL",
    { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}
function fmtHM(ts) {
  return new Date(ts * 1000).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
}
function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
function sum(a) { return a.reduce((x, y) => x + y, 0); }

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

const TRIGGER_HELP = {
  time_price: "Transza wykona się, gdy nadejdzie jej czas ORAZ cena mieści się w zakresie (suwaki czasu i ceny).",
  time: "Transza wykona się o zaplanowanej godzinie, niezależnie od ceny (suwaki czasu, ceny stałe).",
  price: "Transza wykona się, gdy tylko cena wejdzie w zakres (suwaki ceny, czas stały).",
};

// ---------------------------------------------------------------- init

async function loadSettings() {
  SETTINGS = await api("/api/trade/settings");
  const badge = $("#mb-mode");
  badge.textContent = SETTINGS.dry_run ? "DRY-RUN" : "LIVE — realne środki";
  badge.className = "mode-badge " + (SETTINGS.dry_run ? "dry" : "live");
  $("#mb-key").innerHTML = SETTINGS.keys.length
    ? SETTINGS.keys.map(k => `<option value="${k.filename}">${esc(k.name)} — ${esc(k.pubkey.slice(0, 6))}…${esc(k.pubkey.slice(-4))}</option>`).join("")
    : `<option value="">brak kluczy w wallet/</option>`;

  const problems = [];
  if (!SETTINGS.available) problems.push("Brak biblioteki <b>solders</b> — <code>pip install solders</code>.");
  if (!SETTINGS.keys.length) problems.push("Brak kluczy — wrzuć JSON (solana-cli) do katalogu <code>wallet/</code>.");
  const warn = $("#mb-unavailable");
  warn.innerHTML = problems.join("<br>");
  warn.style.display = problems.length ? "" : "none";
  $("#mb-submit").disabled = !!problems.length;
}

async function loadPrice() {
  const p = await api("/api/price");
  $("#mb-price").textContent = p.price_xnt ? p.price_xnt.toFixed(7) + " XNT" : "—";
  return p.price_xnt;
}

function setSide(side) {
  SIDE = side;
  document.querySelectorAll("#mb-side button").forEach(b =>
    b.classList.toggle("on", b.dataset.side === side));
  $("#mb-unit").textContent = side === "buy" ? "XNT" : window.TOKEN;
  refresh();
}

function setTrigger(t) {
  TRIGGER = t;
  document.querySelectorAll("#mb-trigger button").forEach(b =>
    b.classList.toggle("on", b.dataset.trig === t));
  $("#mb-trigger-help").textContent = TRIGGER_HELP[t];
  $("#mb-range-box").style.display = t === "time" ? "none" : "";
  // czasowy -> tylko suwaki czasu; cenowy -> tylko suwaki ceny; oba -> podwojne
  $("#mb-rows").classList.toggle("no-time", t === "price");
  $("#mb-rows").classList.toggle("no-price", t === "time");
  $('#mb-dist .dist-group[data-group="time"]').style.display = t === "price" ? "none" : "";
  $('#mb-dist .dist-group[data-group="price"]').style.display = t === "time" ? "none" : "";
  refresh();
}

// ---------------------------------------------------------------- rozklad transz

function curN() {
  return Math.max(1, Math.min(window.MAX_SLICES, parseInt($("#mb-slices").value) || 1));
}

function initDist(n) {
  W.size = new Array(n).fill(50);
  W.time = new Array(n).fill(50);
  W.price = new Array(n).fill(0);
  resetSkew();
}

function resetSkew(group) {
  document.querySelectorAll("#mb-dist .skew").forEach(s => {
    if (!group || s.dataset.skew === group) s.value = 0;
  });
}

function applySkew(group, pct) {
  const n = W[group].length;
  const s = clamp(pct / 100, -1, 1) * SKEW_SIGN[group];
  if (n <= 1) { W[group] = [group === "price" ? 0 : 50]; return; }
  for (let i = 0; i < n; i++) {
    const ramp = (i / (n - 1)) - 0.5;                       // -0.5 .. +0.5
    W[group][i] = group === "price"
      ? clamp(Math.round(s * 60 * ramp), -90, 90)           // do +-30%
      : clamp(Math.round(50 + s * 100 * ramp), 1, 100);
  }
}

function mixGroup(group) {
  const rnd = () => Math.random();
  W[group] = W[group].map(() => group === "size" ? 5 + Math.floor(rnd() * 96)
    : group === "time" ? 40 + Math.floor(rnd() * 61)        // odstepy 40..100 (ok. 1:2,5)
      : Math.round((rnd() * 2 - 1) * 15));                  // cena -15%..+15%
  resetSkew(group);
}

function evenGroup(group) {
  W[group] = W[group].map(() => group === "price" ? 0 : 50);
  resetSkew(group);
}

function renderRows() {
  const n = W.size.length;
  let html = "";
  for (let i = 0; i < n; i++) {
    html += `<div class="wrow">
      <span class="wlbl">Transza ${i + 1}</span>
      <span class="wcell size">
        <input type="range" data-g="size" data-i="${i}" min="1" max="100" value="${W.size[i]}">
        <span class="wval" data-v="size-${i}">—</span></span>
      <span class="wcell time">
        <input type="range" data-g="time" data-i="${i}" min="1" max="100" value="${W.time[i]}">
        <span class="wval" data-v="time-${i}">—</span></span>
      <span class="wcell price">
        <input type="range" data-g="price" data-i="${i}" min="-90" max="90" value="${W.price[i]}">
        <span class="wval" data-v="price-${i}">0%</span></span>
    </div>`;
  }
  $("#mb-rows").innerHTML = html;
  applyLocks();
  refresh();
}

function syncSliders() {
  document.querySelectorAll("#mb-rows input[type=range]").forEach(el => {
    const v = W[el.dataset.g][+el.dataset.i];
    if (v != null) el.value = v;
  });
}

function applyLocks() {
  GROUPS.forEach(g => {
    const on = LOCK[g];
    document.querySelectorAll(`#mb-rows input[data-g="${g}"]`).forEach(el => { el.disabled = on; });
    const skew = $(`#mb-dist .skew[data-skew="${g}"]`);
    if (skew) skew.disabled = on;
    const btn = $(`#mb-dist .lockbtn[data-lock="${g}"]`);
    if (btn) {
      btn.textContent = on ? "🔒" : "🔓";
      btn.classList.toggle("on", on);
      btn.title = on ? "Suwaki zablokowane — kliknij, aby odblokować" : "Zablokuj suwaki";
    }
    const box = $(`#mb-dist .dist-group[data-group="${g}"]`);
    if (box) box.classList.toggle("locked", on);
  });
}

// ---------------------------------------------------------------- plan

function planData() {
  const total = parseFloat($("#mb-amount").value) || 0;
  const n = W.size.length;
  const startIn = (parseInt($("#mb-start-in").value) || 0) * 60;
  const win = Math.max(1, parseInt($("#mb-window").value) || 1) * 60;
  const start = Math.floor(Date.now() / 1000) + startIn;
  const sumS = sum(W.size) || 1;
  const sumT = sum(W.time) || 1;
  const amounts = W.size.map(w => total * w / sumS);
  const shares = W.size.map(w => w / sumS);
  const gaps = W.time.map(w => win * w / sumT);
  const times = [];
  let acc = start;
  for (let i = 0; i < n; i++) { times.push(Math.round(acc)); acc += gaps[i]; }
  return { total, n, start, end: start + win, win, amounts, shares, gaps, times };
}

function effRange(i) {
  const pmin = parseFloat($("#mb-pmin").value), pmax = parseFloat($("#mb-pmax").value);
  if (!isFinite(pmin) && !isFinite(pmax)) return null;
  const o = W.price[i] || 0;
  const mn = isFinite(pmin) ? pmin * (1 + o / 100) : null;
  const mx = isFinite(pmax) ? pmax * (1 + o / 100) : null;
  return `[${mn != null ? mn.toFixed(7) : "−∞"}, ${mx != null ? mx.toFixed(7) : "+∞"}]`;
}

function refresh() {
  const { total, n, amounts, shares, gaps, times } = planData();
  const unit = SIDE === "buy" ? "XNT" : window.TOKEN;

  // etykiety przy suwakach
  for (let i = 0; i < n; i++) {
    const sz = $(`#mb-rows .wval[data-v="size-${i}"]`);
    if (sz) {
      sz.innerHTML = (total > 0 ? `${fmt(amounts[i], 6)} ${unit} ` : "") +
        `<span class="wpct">${(shares[i] * 100).toFixed(1)}%</span>`;
    }
    const tm = $(`#mb-rows .wval[data-v="time-${i}"]`);
    if (tm) tm.innerHTML = `${fmtHM(times[i])} <span class="wpct">+${(gaps[i] / 60).toFixed(1)} min</span>`;
    const pr = $(`#mb-rows .wval[data-v="price-${i}"]`);
    if (pr) {
      const o = W.price[i] || 0;
      pr.textContent = (o > 0 ? "+" : "") + o + "%";
      pr.className = "wval " + (o === 0 ? "zero" : (o > 0 ? "plus" : "minus"));
    }
  }

  renderPlan({ total, n, amounts, gaps, times, unit });
}

function renderPlan({ total, n, amounts, gaps, times, unit }) {
  if (!(total > 0)) {
    $("#mb-plan").innerHTML = `<span class="muted">Podaj ilość, aby zobaczyć plan.</span>`;
    $("#mb-preview").textContent = "";
    return;
  }
  const showTime = TRIGGER !== "price";
  const showOff = TRIGGER !== "time";
  const hasRange = showOff && ($("#mb-pmin").value || $("#mb-pmax").value);
  const rows = [];
  for (let i = 0; i < n; i++) {
    rows.push(`<tr><td>#${i + 1}</td><td class="mono">${fmt(amounts[i], 6)} ${unit}</td>
      ${showTime ? `<td class="muted">${fmtTime(times[i])}</td>
                    <td class="mono muted">${(gaps[i] / 60).toFixed(1)} min</td>` : ""}
      ${showOff ? `<td class="mono">${(W.price[i] > 0 ? "+" : "") + (W.price[i] || 0)}%</td>` : ""}
      ${hasRange ? `<td class="mono muted">${effRange(i)}</td>` : ""}</tr>`);
  }
  $("#mb-plan").innerHTML = `<table class="tbl"><thead><tr>
      <th>Transza</th><th>Ilość</th>
      ${showTime ? "<th>Planowana na</th><th>Odstęp</th>" : ""}
      ${showOff ? "<th>±%</th>" : ""}
      ${hasRange ? "<th>Zakres transzy</th>" : ""}</tr></thead>
      <tbody>${rows.join("")}</tbody></table>`;

  const pmin = $("#mb-pmin").value, pmax = $("#mb-pmax").value;
  const range = (showOff && (pmin || pmax)) ? ` w zakresie ${pmin || "—"}..${pmax || "—"} XNT` : "";
  const trigTxt = { time_price: "czasowo-cenowy", time: "czasowy", price: "cenowy" }[TRIGGER];
  $("#mb-preview").innerHTML =
    `${SIDE === "buy" ? "Kupno za" : "Sprzedaż"} <b>${fmt(total, 6)} ${unit}</b>
     w ${n} transzach${range}, wyzwalacz <b>${trigTxt}</b>,
     tryb <b>${SETTINGS?.dry_run ? "DRY-RUN" : "LIVE"}</b>.`;
}

// ---------------------------------------------------------------- bindy formularza

["mb-amount", "mb-start-in", "mb-window", "mb-pmin", "mb-pmax"]
  .forEach(id => $("#" + id).addEventListener("input", refresh));
document.querySelectorAll("#mb-side button").forEach(b =>
  b.addEventListener("click", () => setSide(b.dataset.side)));
document.querySelectorAll("#mb-trigger button").forEach(b =>
  b.addEventListener("click", () => setTrigger(b.dataset.trig)));

$("#mb-slices").addEventListener("input", () => { initDist(curN()); renderRows(); });

$("#mb-rows").addEventListener("input", (e) => {
  const el = e.target.closest("input[type=range]");
  if (!el) return;
  const g = el.dataset.g;
  if (LOCK[g]) { el.value = W[g][+el.dataset.i]; return; }
  W[g][+el.dataset.i] = +el.value;
  resetSkew(g);
  refresh();
});

$("#mb-dist").addEventListener("input", (e) => {
  const el = e.target.closest(".skew");
  if (!el) return;
  const g = el.dataset.skew;
  if (LOCK[g]) { el.value = 0; return; }
  applySkew(g, +el.value);
  syncSliders();
  refresh();
});

$("#mb-dist").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-mix],button[data-even],button[data-lock]");
  if (!btn) return;
  if (btn.dataset.lock) {
    LOCK[btn.dataset.lock] = !LOCK[btn.dataset.lock];
    applyLocks();
    return;
  }
  const g = btn.dataset.mix || btn.dataset.even;
  if (LOCK[g]) return toast("Suwaki zablokowane — odblokuj 🔒", "err");
  btn.dataset.mix ? mixGroup(g) : evenGroup(g);
  syncSliders();
  refresh();
});

// ---------------------------------------------------------------- zlecenia

function sliceDot(s) {
  const cls = { filled: "ok", pending: "pend", skipped: "skip", failed: "fail" }[s.status] || "pend";
  const title = `#${s.idx + 1}: ${s.status} · ${fmt(s.amount, 6)} · plan ${fmtTime(s.scheduled_at)}` +
    (s.price_offset_pct ? ` · offset ${s.price_offset_pct > 0 ? "+" : ""}${s.price_offset_pct}%` : "") +
    (s.executed_price ? ` @ ${s.executed_price.toFixed(7)}` : "") +
    (s.error ? ` · ${s.error}` : "");
  return `<span class="sdot ${cls}" title="${esc(title)}"></span>`;
}

function orderCard(o) {
  const unit = o.amount_unit === "xnt" ? "XNT" : o.token;
  const statusCls = { running: "amber", done: "green", cancelled: "muted", failed: "red" }[o.status] || "";
  const filledSlices = o.slices.filter(s => s.status === "filled");
  const avg = filledSlices.length
    ? filledSlices.reduce((a, s) => a + (s.executed_price || 0), 0) / filledSlices.length : null;
  const sigs = filledSlices.filter(s => s.tx_signature).length;
  const actions = o.status === "running"
    ? `<button class="btn small danger-ghost" onclick="cancelOrder(${o.id})">✕ Anuluj</button>`
    : `<button class="iconbtn" title="ukryj" onclick="hideOrder(${o.id})">👁</button>
       <button class="iconbtn" title="usuń" onclick="deleteOrder(${o.id})">🗑</button>`;
  return `<div class="order-card">
    <div class="o-head">
      <b>#${o.id} ${o.side === "buy" ? "KUPNO" : "SPRZEDAŻ"} ${esc(o.token)}</b>
      <span class="tag ${statusCls}">${o.status}</span>
      ${o.dry_run ? `<span class="tag">DRY-RUN</span>` : `<span class="tag red">LIVE</span>`}
      <span class="spacer"></span>${actions}
    </div>
    <div class="o-body">
      <div class="slices">${o.slices.map(sliceDot).join("")}</div>
      <div class="o-stats">
        <span>Łącznie: <b>${fmt(o.total_amount, 6)} ${unit}</b></span>
        <span>Wykonane: <b>${o.filled}/${o.num_slices}</b> (${fmt(o.done_amount, 6)} ${unit})</span>
        <span>Śr. cena: <b>${avg ? avg.toFixed(7) : "—"}</b></span>
        <span>Zakres: <b>${o.price_min ?? "—"} .. ${o.price_max ?? "—"}</b></span>
        <span>Wyzwalacz: <b>${o.trigger_mode}</b></span>
        <span>Okno: <b>${fmtTime(o.window_start)} – ${fmtTime(o.window_end)}</b></span>
        <span>Klucz: <b>${esc(o.key_file)}</b></span>
        ${sigs ? `<span>Transakcji on-chain: <b>${sigs}</b></span>` : ""}
        ${o.note ? `<span>Notatka: ${esc(o.note)}</span>` : ""}
      </div>
    </div>
  </div>`;
}

async function loadOrders() {
  const d = await api("/api/multibot" + (SHOW_HIDDEN ? "?hidden=1" : ""));
  $("#mb-orders").innerHTML = d.orders.length
    ? d.orders.map(orderCard).join("")
    : `<span class="muted">brak zleceń</span>`;
}

window.cancelOrder = async (id) => {
  if (!confirm(`Anulować zlecenie #${id}? Niewykonane transze zostaną pominięte.`)) return;
  await api(`/api/multibot/${id}/cancel`, { method: "POST" });
  toast("Zlecenie anulowane", "ok");
  await loadOrders();
};
window.hideOrder = async (id) => {
  try {
    await api(`/api/multibot/${id}/hide`, { method: "POST", body: { hidden: true } });
    await loadOrders();
  } catch (e) { toast(e.message, "err"); }
};
window.deleteOrder = async (id) => {
  if (!confirm(`Usunąć trwale zlecenie #${id}?`)) return;
  try {
    await api(`/api/multibot/${id}`, { method: "DELETE" });
    await loadOrders();
  } catch (e) { toast(e.message, "err"); }
};

$("#mb-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const { total, n, start, end } = planData();
  if (!(total > 0)) return toast("Podaj ilość", "err");
  const key_file = $("#mb-key").value;
  if (!key_file) return toast("Brak klucza w katalogu wallet/", "err");

  const live = SETTINGS && !SETTINGS.dry_run;
  const unit = SIDE === "buy" ? "XNT" : window.TOKEN;
  const msg = `Uruchomić MultiBOT?\n\n${SIDE === "buy" ? "KUPNO za" : "SPRZEDAŻ"} ${total} ${unit}` +
    `\nw ${n} transzach, wyzwalacz: ${TRIGGER}` +
    (live ? "\n\nUWAGA: tryb LIVE — transze wydadzą PRAWDZIWE środki!" : "\n\nTryb DRY-RUN — nic nie zostanie wysłane.");
  if (!confirm(msg)) return;

  try {
    const r = await api("/api/multibot", {
      method: "POST",
      body: {
        side: SIDE, token: window.TOKEN, key_file, total_amount: total,
        num_slices: n, window_start: start, window_end: end,
        trigger_mode: TRIGGER,
        weights: W.size.slice(0, n),
        time_weights: W.time.slice(0, n),
        offsets: TRIGGER === "time" ? W.price.map(() => 0) : W.price.slice(0, n),
        price_min: TRIGGER === "time" ? null : ($("#mb-pmin").value || null),
        price_max: TRIGGER === "time" ? null : ($("#mb-pmax").value || null),
        note: $("#mb-note").value || null,
      },
    });
    toast(`MultiBOT #${r.id} uruchomiony`, "ok");
    $("#mb-amount").value = ""; $("#mb-note").value = "";
    refresh();
    await loadOrders();
  } catch (err) { toast(err.message, "err"); }
});

$("#mb-refresh").addEventListener("click", () => { loadPrice(); loadOrders(); });
$("#mb-show-hidden").addEventListener("change", (e) => { SHOW_HIDDEN = e.target.checked; loadOrders(); });

initDist(curN());
renderRows();
setSide("buy");
setTrigger("time_price");
loadSettings().then(refresh).catch(e => toast(e.message, "err"));
loadPrice().catch(() => {});
loadOrders().catch(e => toast(e.message, "err"));
setInterval(() => { loadPrice().catch(() => {}); loadOrders().catch(() => {}); }, 15000);
