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
   price -90..90 (offset % zakresu cenowego per transza).
   LINK = grupy sprzezone: ruch suwaka w jednej przesuwa te same transze
   w pozostalych polaczonych grupach (np. dluzszy odstep = wieksza kwota). */
const W = { size: [], time: [], price: [] };
const LINK = { size: false, time: false, price: false };

const GROUPS = ["size", "time", "price"];
const GROUP_LABEL = { size: "wielkość", time: "czas", price: "cena" };
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
  // Suwaki czasu zostaja TEZ w trybie cenowym — tam nie ustawiaja godzin,
  // tylko dziela minimalny odstep miedzy transzami (rytm cooldownu).
  $("#mb-rows").classList.remove("no-time");
  $("#mb-rows").classList.toggle("no-price", t === "time");
  $('#mb-dist .dist-group[data-group="time"]').style.display = "";
  $('#mb-dist .dist-group[data-group="price"]').style.display = t === "time" ? "none" : "";
  $("#mb-time-title").textContent = t === "price"
    ? "Czas — minimalne odstępy" : "Czas — odstępy transz";
  $("#mb-interval-help").textContent = t === "price"
    ? "W trybie cenowym to jedyny hamulec: bez odstępu wszystkie transze złapią warunek naraz i wykonają się w tej samej sekundzie."
    : "Dodatkowa blokada ponad harmonogram — kolejna transza nie ruszy szybciej niż tyle po poprzedniej.";
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

/* ---------- laczenie suwakow (sprzezenie grup) ----------
   Wspolna skala t: -1 = suwak do konca w lewo, 0 = srodek, +1 = w prawo.
   size/time: 1..100 (srodek 50), price: pelne wychylenie = +-30%. */
function toT(g, v) { return g === "price" ? v / 30 : (v - 50) / 50; }
function fromT(g, t) {
  return g === "price" ? clamp(Math.round(t * 30), -90, 90)
    : clamp(Math.round(50 + t * 50), 1, 100);
}

function linkedGroups() { return GROUPS.filter(g => LINK[g]); }

/** Przenosi pozycje suwaka transzy `i` z grupy `from` na pozostale polaczone. */
function propagate(from, i) {
  if (!LINK[from]) return;
  const t = toT(from, W[from][i]);
  linkedGroups().forEach(g => { if (g !== from) W[g][i] = fromT(g, t); });
}

/** To samo dla calej grupy (Mix / Rowno / skos / wlaczenie polaczenia). */
function propagateAll(from) {
  if (!LINK[from]) return;
  W[from].forEach((_, i) => propagate(from, i));
  linkedGroups().forEach(g => { if (g !== from) resetSkew(g); });
}

function renderLinkInfo() {
  const on = linkedGroups();
  const box = $("#mb-link-info");
  if (!on.length) { box.textContent = ""; box.style.display = "none"; return; }
  box.style.display = "";
  box.innerHTML = on.length > 1
    ? `🔗 Suwaki połączone: <b>${on.map(g => GROUP_LABEL[g]).join(" + ")}</b>
       — ruch jednego przesuwa pozostałe.`
    : `<span class="muted">🔗 Grupa <b>${GROUP_LABEL[on[0]]}</b> czeka na parę —
       włącz „Łącz" jeszcze w jednej grupie, aby suwaki chodziły razem.</span>`;
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
  applyLinks();
  refresh();
}

function syncSliders() {
  document.querySelectorAll("#mb-rows input[type=range]").forEach(el => {
    const v = W[el.dataset.g][+el.dataset.i];
    if (v != null) el.value = v;
  });
}

function applyLinks() {
  GROUPS.forEach(g => {
    const on = LINK[g];
    const btn = $(`#mb-dist .linkbtn[data-link="${g}"]`);
    if (btn) {
      btn.classList.toggle("on", on);
      btn.title = on ? "Odłącz suwaki tej grupy" : "Połącz suwaki z inną grupą";
    }
    const box = $(`#mb-dist .dist-group[data-group="${g}"]`);
    if (box) box.classList.toggle("linked", on);
    document.querySelectorAll(`#mb-rows .wcell.${g}`).forEach(el => el.classList.toggle("linked", on));
  });
  renderLinkInfo();
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

  // minimalne odstepy — lustro multibot._slice_gaps: budzet = interwal*(n-1)
  // rozdzielony pierwszymi n-1 wagami czasu, wiec przy rownych wagach kazdy
  // odstep = dokladnie ustawiony interwal
  const minInterval = Math.max(0, parseFloat($("#mb-interval").value) || 0) * 60;
  const minGaps = new Array(n).fill(0);
  if (n > 1 && minInterval > 0) {
    const head = W.time.slice(0, n - 1);
    const sumH = sum(head) || (n - 1);
    const budget = minInterval * (n - 1);
    for (let i = 1; i < n; i++) minGaps[i] = Math.round(budget * head[i - 1] / sumH);
  }
  return { total, n, start, end: start + win, win, amounts, shares, gaps, times,
           minInterval, minGaps };
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
  const { total, n, amounts, shares, gaps, times, win, minInterval, minGaps } = planData();
  const unit = SIDE === "buy" ? "XNT" : window.TOKEN;

  // etykiety przy suwakach
  for (let i = 0; i < n; i++) {
    const sz = $(`#mb-rows .wval[data-v="size-${i}"]`);
    if (sz) {
      sz.innerHTML = (total > 0 ? `${fmt(amounts[i], 6)} ${unit} ` : "") +
        `<span class="wpct">${(shares[i] * 100).toFixed(1)}%</span>`;
    }
    const tm = $(`#mb-rows .wval[data-v="time-${i}"]`);
    if (tm) {
      tm.innerHTML = TRIGGER === "price"
        ? (i === 0 ? `<span class="wpct">bez czekania</span>`
          : `min. <span class="wpct">${(minGaps[i] / 60).toFixed(1)} min</span> przerwy`)
        : `${fmtHM(times[i])} <span class="wpct">+${(gaps[i] / 60).toFixed(1)} min</span>`;
    }
    const pr = $(`#mb-rows .wval[data-v="price-${i}"]`);
    if (pr) {
      const o = W.price[i] || 0;
      pr.textContent = (o > 0 ? "+" : "") + o + "%";
      pr.className = "wval " + (o === 0 ? "zero" : (o > 0 ? "plus" : "minus"));
    }
  }

  renderPlan({ total, n, amounts, gaps, times, unit, win, minInterval, minGaps });
}

function renderPlan({ total, n, amounts, gaps, times, unit, win, minInterval, minGaps }) {
  if (!(total > 0)) {
    $("#mb-plan").innerHTML = `<span class="muted">Podaj ilość, aby zobaczyć plan.</span>`;
    $("#mb-preview").textContent = "";
    return;
  }
  const showTime = TRIGGER !== "price";
  const showOff = TRIGGER !== "time";
  const showGap = minInterval > 0;
  const hasRange = showOff && ($("#mb-pmin").value || $("#mb-pmax").value);
  const rows = [];
  for (let i = 0; i < n; i++) {
    rows.push(`<tr><td>#${i + 1}</td><td class="mono">${fmt(amounts[i], 6)} ${unit}</td>
      ${showTime ? `<td class="muted">${fmtTime(times[i])}</td>
                    <td class="mono muted">${(gaps[i] / 60).toFixed(1)} min</td>` : ""}
      ${showGap ? `<td class="mono muted">${i === 0 ? "—"
                    : "≥ " + (minGaps[i] / 60).toFixed(1) + " min"}</td>` : ""}
      ${showOff ? `<td class="mono">${(W.price[i] > 0 ? "+" : "") + (W.price[i] || 0)}%</td>` : ""}
      ${hasRange ? `<td class="mono muted">${effRange(i)}</td>` : ""}</tr>`);
  }
  $("#mb-plan").innerHTML = `<table class="tbl"><thead><tr>
      <th>Transza</th><th>Ilość</th>
      ${showTime ? "<th>Planowana na</th><th>Odstęp</th>" : ""}
      ${showGap ? "<th>Min. przerwa</th>" : ""}
      ${showOff ? "<th>±%</th>" : ""}
      ${hasRange ? "<th>Zakres transzy</th>" : ""}</tr></thead>
      <tbody>${rows.join("")}</tbody></table>`;

  const pmin = $("#mb-pmin").value, pmax = $("#mb-pmax").value;
  const range = (showOff && (pmin || pmax)) ? ` w zakresie ${pmin || "—"}..${pmax || "—"} XNT` : "";
  const trigTxt = { time_price: "czasowo-cenowy", time: "czasowy", price: "cenowy" }[TRIGGER];
  // suma minimalnych przerw musi zmiescic sie w oknie — inaczej ostatnie
  // transze dostana 'skipped', gdy okno minie
  const needed = sum(minGaps);
  const warn = needed > win
    ? `<div class="warn-line">⚠ Przy tej przerwie ostatnie transze nie zdążą:
       potrzeba ≥ <b>${(needed / 60).toFixed(0)} min</b> okna, a masz
       <b>${(win / 60).toFixed(0)} min</b>. Wydłuż czas trwania albo skróć odstęp.</div>`
    : "";
  const gapTxt = minInterval > 0
    ? `, min. przerwa <b>${(minInterval / 60).toFixed(1)} min</b>` : "";
  $("#mb-preview").innerHTML =
    `${SIDE === "buy" ? "Kupno za" : "Sprzedaż"} <b>${fmt(total, 6)} ${unit}</b>
     w ${n} transzach${range}, wyzwalacz <b>${trigTxt}</b>${gapTxt},
     tryb <b>${SETTINGS?.dry_run ? "DRY-RUN" : "LIVE"}</b>.${warn}`;
}

// ---------------------------------------------------------------- bindy formularza

["mb-amount", "mb-start-in", "mb-window", "mb-pmin", "mb-pmax", "mb-interval"]
  .forEach(id => $("#" + id).addEventListener("input", refresh));
document.querySelectorAll("#mb-side button").forEach(b =>
  b.addEventListener("click", () => setSide(b.dataset.side)));
document.querySelectorAll("#mb-trigger button").forEach(b =>
  b.addEventListener("click", () => setTrigger(b.dataset.trig)));

$("#mb-slices").addEventListener("input", () => { initDist(curN()); renderRows(); });

$("#mb-rows").addEventListener("input", (e) => {
  const el = e.target.closest("input[type=range]");
  if (!el) return;
  const g = el.dataset.g, i = +el.dataset.i;
  W[g][i] = +el.value;
  resetSkew(g);
  propagate(g, i);          // sprzezone grupy jada za tym suwakiem
  syncSliders();
  refresh();
});

$("#mb-dist").addEventListener("input", (e) => {
  const el = e.target.closest(".skew");
  if (!el) return;
  const g = el.dataset.skew;
  applySkew(g, +el.value);
  propagateAll(g);
  syncSliders();
  refresh();
});

$("#mb-dist").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-mix],button[data-even],button[data-link]");
  if (!btn) return;
  if (btn.dataset.link) {
    const g = btn.dataset.link;
    LINK[g] = !LINK[g];
    if (LINK[g]) {
      // dolaczana grupa przejmuje uklad suwakow od juz polaczonej
      const master = linkedGroups().find(x => x !== g);
      if (master) { propagateAll(master); syncSliders(); refresh(); }
    }
    applyLinks();
    return;
  }
  const g = btn.dataset.mix || btn.dataset.even;
  btn.dataset.mix ? mixGroup(g) : evenGroup(g);
  propagateAll(g);
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
        ${o.min_interval_s ? `<span>Min. przerwa: <b>${(o.min_interval_s / 60).toFixed(1)} min</b></span>` : ""}
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
  const { total, n, start, end, win, minInterval, minGaps } = planData();
  if (!(total > 0)) return toast("Podaj ilość", "err");
  if (sum(minGaps) > win &&
      !confirm(`Minimalne przerwy (${(sum(minGaps) / 60).toFixed(0)} min) nie mieszczą się ` +
               `w oknie ${(win / 60).toFixed(0)} min — ostatnie transze zostaną pominięte. ` +
               `Uruchomić mimo to?`)) return;
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
        min_interval_s: Math.round(minInterval),
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
