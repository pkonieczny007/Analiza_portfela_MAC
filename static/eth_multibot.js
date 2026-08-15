/* MultiBOT ETH — zlecenie dzielone na transze na Uniswap v3 (Base).

   Port UI z X1 (`static/multibot.js`) na pare WETH/USDC: te same trzy grupy
   suwakow (wielkosc pozycji / odstepy czasu / przesuniecie ceny), przyciski
   Rowno / Mix / Lacz, suwak skosu i minimalny odstep miedzy transzami.

   Czysta logika rozkladu (podzial kwoty, dlugosci odstepow, sprzezenie grup)
   siedzi na gorze pliku i NIE dotyka DOM — dzieki temu ten sam kod da sie
   odpalic w node i sprawdzic, ze suma transz rowna sie kwocie, a odstepy
   mieszcza sie w oknie. Warstwa DOM startuje dopiero w `boot()`. */
"use strict";

const GROUPS = ["size", "time", "price"];
const GROUP_LABEL = { size: "wielkość", time: "czas", price: "cena" };
// time: prawo na suwaku skosu = "gesciej na koncu", czyli KROTSZE odstepy
// na koncu — stad odwrocony znak wzgledem pozostalych grup
const SKEW_SIGN = { size: 1, time: -1, price: 1 };

// ---------------------------------------------------------------- czysta logika

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
function sum(a) { return a.reduce((x, y) => x + y, 0); }

/** Wartosc "neutralna" grupy: srodek skali wag, zero przesuniecia ceny. */
function neutral(group) { return group === "price" ? 0 : 50; }

function evenWeights(group, n) {
  return new Array(n).fill(neutral(group));
}

/** Rampa od pierwszej do ostatniej transzy; pct z suwaka skosu (-100..100). */
function skewWeights(group, pct, n) {
  const s = clamp(pct / 100, -1, 1) * SKEW_SIGN[group];
  if (n <= 1) return [neutral(group)];
  const out = [];
  for (let i = 0; i < n; i++) {
    const ramp = (i / (n - 1)) - 0.5;                        // -0.5 .. +0.5
    out.push(group === "price"
      ? clamp(Math.round(s * 60 * ramp), -90, 90)            // pelne wychylenie = +-30%
      : clamp(Math.round(50 + s * 100 * ramp), 1, 100));
  }
  return out;
}

/** Losowy uklad; `rnd` wstrzykiwane, zeby test mogl podac deterministyczny. */
function mixWeights(group, n, rnd) {
  const r = rnd || Math.random;
  const out = [];
  for (let i = 0; i < n; i++) {
    out.push(group === "size" ? 5 + Math.floor(r() * 96)
      : group === "time" ? 40 + Math.floor(r() * 61)         // odstepy 40..100 (ok. 1:2,5)
        : Math.round((r() * 2 - 1) * 15));                   // cena -15%..+15%
  }
  return out;
}

/* Sprzezenie grup ("Lacz") liczy sie przez wspolna skale t ∈ <-1,1>:
   -1 = suwak do konca w lewo, 0 = srodek, +1 = w prawo. size/time to 1..100
   (srodek 50), price to +-30%, wiec srodek suwaka ceny = 0 %. */
function toT(g, v) { return g === "price" ? v / 30 : (v - 50) / 50; }
function fromT(g, t) {
  return g === "price" ? clamp(Math.round(t * 30), -90, 90)
    : clamp(Math.round(50 + t * 50), 1, 100);
}

/** Przenosi pozycje suwaka transzy `i` z grupy `from` na pozostale polaczone. */
function propagate(W, links, from, i) {
  if (!links[from]) return W;
  const t = toT(from, W[from][i]);
  GROUPS.forEach(g => { if (g !== from && links[g]) W[g][i] = fromT(g, t); });
  return W;
}

/** To samo dla calej grupy (Mix / Rowno / skos / wlaczenie polaczenia). */
function propagateAll(W, links, from) {
  if (!links[from]) return W;
  W[from].forEach((_, i) => propagate(W, links, from, i));
  return W;
}

/** Podzial kwoty wagami. Ostatnia transza bierze reszte — inaczej blad
    zaokraglenia sprawia, ze suma transz != kwota lacznego zlecenia. */
function splitAmount(total, weights) {
  const s = sum(weights) || 1;
  const out = weights.map(w => total * w / s);
  if (out.length) out[out.length - 1] = total - sum(out.slice(0, -1));
  return out;
}

/** Wagi czasu to DLUGOSCI ODSTEPOW, normalizowane do okna — mix zmienia
    rytm, nigdy sumy. Ostatni odstep bierze reszte, wiec suma == okno. */
function splitGaps(win, timeWeights) {
  const s = sum(timeWeights) || 1;
  const out = timeWeights.map(w => win * w / s);
  if (out.length) out[out.length - 1] = win - sum(out.slice(0, -1));
  return out;
}

/** Pierwsza transza startuje na poczatku okna, kazda kolejna po odstepie
    poprzedniej — dlatego ostatni odstep zostaje "za" ostatnia transza. */
function sliceTimes(start, gaps) {
  const out = [];
  let acc = start;
  for (let i = 0; i < gaps.length; i++) { out.push(Math.round(acc)); acc += gaps[i]; }
  return out;
}

/** Lustro `multibot._slice_gaps`: budzet `interwal * (n-1)` rozdzielony
    pierwszymi n-1 wagami czasu, wiec przy rownych suwakach kazda przerwa
    to dokladnie ustawiony interwal. Pierwsza transza nie czeka. */
function minGapsFor(minInterval, timeWeights) {
  const n = timeWeights.length;
  const out = new Array(n).fill(0);
  if (n < 2 || !(minInterval > 0)) return out;
  const head = timeWeights.slice(0, n - 1);
  const sumH = sum(head) || (n - 1);
  const budget = minInterval * (n - 1);
  for (let i = 1; i < n; i++) out[i] = Math.round(budget * head[i - 1] / sumH);
  return out;
}

// ---------------------------------------------------------------- stan UI

const $ = (s) => document.querySelector(s);
let SIDE = "buy";
let TRIGGER = "time_price";
let SETTINGS = null;
let SHOW_HIDDEN = false;
let PLAN_N = 0;              // liczba transz aktualnie WYRENDEROWANA w tabeli

/* Rozklad transz: size/time 1..100 (wagi), price -90..90 (offset % zakresu).
   LINK = grupy sprzezone przez wspolna skale t. */
const W = { size: [], time: [], price: [] };
const LINK = { size: false, time: false, price: false };

const TRIGGER_HELP = {
  time_price: "Transza wykona się, gdy nadejdzie jej czas ORAZ cena mieści się w zakresie (suwaki czasu i ceny).",
  time: "Transza wykona się o zaplanowanej godzinie, niezależnie od ceny (suwaki czasu, ceny stałe).",
  price: "Transza wykona się, gdy tylko cena wejdzie w zakres (suwaki ceny, czas stały).",
};

function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function fmt(x, d = 4) {
  return x == null || !isFinite(x) ? "—"
    : Number(x).toLocaleString("pl-PL", { maximumFractionDigits: d });
}
/* Cena to USDC za WETH (rzad wielkosci ~1900) — 7 miejsc jak przy X1 byloby
   szumem, dwa wystarcza i czytaja sie jak kurs z gieldy. */
function fmtPrice(x) {
  return x == null || !isFinite(Number(x)) ? "—"
    : Number(x).toLocaleString("pl-PL", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleString("pl-PL",
    { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}
function fmtHM(ts) {
  return new Date(ts * 1000).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
}

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

/* Kupno = wydajemy USDC (quote), sprzedaz = wydajemy WETH (base). Jednostka
   zlecenia to zawsze token WYDAWANY, wiec idzie za wybrana strona. */
function spendUnit() { return SIDE === "buy" ? window.QUOTE_TOKEN : window.BASE_TOKEN; }
/* USDC liczymy w centach, WETH w mikro-eterach — jedna liczba miejsc po
   przecinku dla obu jednostek albo gubi grosze, albo pokazuje szum. */
function unitDigits(unit) { return unit === window.QUOTE_TOKEN ? 2 : 6; }
function fmtAmt(x, unit) { return fmt(x, unitDigits(unit)); }

/** Jednostka zlecenia z listy — backend moze podac symbol albo rolę. */
function unitLabel(o) {
  const u = String(o.amount_unit || "").toLowerCase();
  if (u === String(window.QUOTE_TOKEN).toLowerCase() || u === "quote" || u === "usdc") return window.QUOTE_TOKEN;
  if (u === String(window.BASE_TOKEN).toLowerCase() || u === "base" || u === "weth") return window.BASE_TOKEN;
  return o.side === "buy" ? window.QUOTE_TOKEN : window.BASE_TOKEN;
}

// ---------------------------------------------------------------- init

async function loadSettings() {
  SETTINGS = await api("/api/eth/settings");
  const badge = $("#ethmb-mode");
  badge.textContent = SETTINGS.dry_run ? "DRY-RUN" : "LIVE — realne środki";
  badge.className = "mode-badge " + (SETTINGS.dry_run ? "dry" : "live");

  $("#ethmb-key").innerHTML = SETTINGS.keys.length
    ? SETTINGS.keys.map(k => `<option value="${esc(k.filename)}">${esc(k.name)} — ${esc(String(k.address).slice(0, 6))}…${esc(String(k.address).slice(-4))}</option>`).join("")
    : `<option value="">brak kluczy w wallet_evm/</option>`;

  const problems = [];
  if (!SETTINGS.available) problems.push("Brak biblioteki <b>eth-account</b> — <code>pip install eth-account</code>.");
  if (!SETTINGS.keys.length) problems.push("Brak kluczy — wrzuć plik do katalogu <code>wallet_evm/</code>.");
  const warn = $("#ethmb-unavailable");
  warn.innerHTML = problems.join("<br>");
  warn.style.display = problems.length ? "" : "none";
  $("#ethmb-submit").disabled = !!problems.length;

  // publiczny RPC to nie blad konfiguracji, tylko ryzyko HTTP 429 przy
  // odpytywaniu co kilkanascie sekund — stad osobny, dyskretny pasek
  const rpc = $("#ethmb-rpc-note");
  rpc.style.display = SETTINGS.rpc_public ? "" : "none";
}

async function loadPrice() {
  try {
    const p = await api("/api/eth/price");
    $("#ethmb-price").textContent = p.price
      ? fmtPrice(p.price) + " " + window.QUOTE_TOKEN : "—";
  } catch (e) { $("#ethmb-price").textContent = "—"; }
}

function setSide(side) {
  SIDE = side;
  document.querySelectorAll("#ethmb-side button").forEach(b =>
    b.classList.toggle("on", b.dataset.side === side));
  const unit = spendUnit();
  $("#ethmb-unit").textContent = unit;
  $("#ethmb-amount").placeholder = side === "buy" ? "np. 250" : "np. 0.05";
  $("#ethmb-amount").step = "any";
  refresh();
}

function setTrigger(t) {
  TRIGGER = t;
  document.querySelectorAll("#ethmb-trigger button").forEach(b =>
    b.classList.toggle("on", b.dataset.trig === t));
  $("#ethmb-trigger-help").textContent = TRIGGER_HELP[t];
  $("#ethmb-range-box").style.display = t === "time" ? "none" : "";
  // Suwaki czasu zostaja TEZ w trybie cenowym — tam nie ustawiaja godzin,
  // tylko dziela minimalny odstep miedzy transzami (rytm cooldownu).
  $("#ethmb-rows").classList.remove("no-time");
  $("#ethmb-rows").classList.toggle("no-price", t === "time");
  $('#ethmb-dist .dist-group[data-group="time"]').style.display = "";
  $('#ethmb-dist .dist-group[data-group="price"]').style.display = t === "time" ? "none" : "";
  $("#ethmb-time-title").textContent = t === "price"
    ? "Czas — minimalne odstępy" : "Czas — odstępy transz";
  $("#ethmb-interval-help").textContent = t === "price"
    ? "W trybie cenowym to jedyny hamulec: bez odstępu wszystkie transze złapią warunek naraz i wykonają się w tej samej sekundzie."
    : "Dodatkowa blokada ponad harmonogram — kolejna transza nie ruszy szybciej niż tyle po poprzedniej.";
  refresh();
}

// ---------------------------------------------------------------- rozklad transz

function curN() {
  return Math.max(1, Math.min(window.MAX_SLICES || 20,
    parseInt($("#ethmb-slices").value) || 1));
}

function initDist(n) {
  GROUPS.forEach(g => { W[g] = evenWeights(g, n); });
  resetSkew();
}

function resetSkew(group) {
  document.querySelectorAll("#ethmb-dist .skew").forEach(s => {
    if (!group || s.dataset.skew === group) s.value = 0;
  });
}

/* Tabela transz przebudowuje sie WYLACZNIE gdy zmieni sie ich liczba.
   Gdyby refresh() przerysowywal wiersze, przegladarka gubilaby uchwyt
   przeciaganego suwaka w pol ruchu — stad `PLAN_N` jako straznik. */
function ensureRows() {
  const n = curN();
  if (n === PLAN_N && W.size.length === n) { refresh(); return; }
  PLAN_N = n;
  initDist(n);          // zmiana liczby transz resetuje rozklad do rownego
  renderRows();
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
  $("#ethmb-rows").innerHTML = html;
  applyLinks();
  refresh();
}

function syncSliders() {
  document.querySelectorAll("#ethmb-rows input[type=range]").forEach(el => {
    const v = W[el.dataset.g][+el.dataset.i];
    if (v != null) el.value = v;
  });
}

function linkedGroups() { return GROUPS.filter(g => LINK[g]); }

function renderLinkInfo() {
  const on = linkedGroups();
  const box = $("#ethmb-link-info");
  if (!on.length) { box.textContent = ""; box.style.display = "none"; return; }
  box.style.display = "";
  box.innerHTML = on.length > 1
    ? `🔗 Suwaki połączone: <b>${on.map(g => GROUP_LABEL[g]).join(" + ")}</b>
       — ruch jednego przesuwa pozostałe.`
    : `<span class="muted">🔗 Grupa <b>${GROUP_LABEL[on[0]]}</b> czeka na parę —
       włącz „Łącz" jeszcze w jednej grupie, aby suwaki chodziły razem.</span>`;
}

function applyLinks() {
  GROUPS.forEach(g => {
    const on = LINK[g];
    const btn = $(`#ethmb-dist .linkbtn[data-link="${g}"]`);
    if (btn) {
      btn.classList.toggle("on", on);
      btn.title = on ? "Odłącz suwaki tej grupy" : "Połącz suwaki z inną grupą";
    }
    const box = $(`#ethmb-dist .dist-group[data-group="${g}"]`);
    if (box) box.classList.toggle("linked", on);
    document.querySelectorAll(`#ethmb-rows .wcell.${g}`).forEach(el => el.classList.toggle("linked", on));
  });
  renderLinkInfo();
}

// ---------------------------------------------------------------- plan

function planData() {
  const total = parseFloat($("#ethmb-amount").value) || 0;
  const n = W.size.length;
  const startIn = (parseInt($("#ethmb-start-in").value) || 0) * 60;
  const win = Math.max(1, parseInt($("#ethmb-window").value) || 1) * 60;
  const start = Math.floor(Date.now() / 1000) + startIn;
  const amounts = splitAmount(total, W.size);
  const sumS = sum(W.size) || 1;
  const shares = W.size.map(w => w / sumS);
  const gaps = splitGaps(win, W.time);
  const times = sliceTimes(start, gaps);
  const minInterval = Math.max(0, parseFloat($("#ethmb-interval").value) || 0) * 60;
  const minGaps = minGapsFor(minInterval, W.time);
  return { total, n, start, end: start + win, win, amounts, shares, gaps, times,
           minInterval, minGaps };
}

/** Zakres cenowy transzy po jej wlasnym przesunieciu (USDC za WETH). */
function effRange(i) {
  const pmin = parseFloat($("#ethmb-pmin").value), pmax = parseFloat($("#ethmb-pmax").value);
  if (!isFinite(pmin) && !isFinite(pmax)) return null;
  const o = W.price[i] || 0;
  const mn = isFinite(pmin) ? pmin * (1 + o / 100) : null;
  const mx = isFinite(pmax) ? pmax * (1 + o / 100) : null;
  return `[${mn != null ? fmtPrice(mn) : "−∞"}, ${mx != null ? fmtPrice(mx) : "+∞"}]`;
}

function refresh() {
  const { total, n, amounts, shares, gaps, times, win, minInterval, minGaps } = planData();
  const unit = spendUnit();

  for (let i = 0; i < n; i++) {
    const sz = $(`#ethmb-rows .wval[data-v="size-${i}"]`);
    if (sz) {
      sz.innerHTML = (total > 0 ? `${fmtAmt(amounts[i], unit)} ${esc(unit)} ` : "") +
        `<span class="wpct">${(shares[i] * 100).toFixed(1)}%</span>`;
    }
    const tm = $(`#ethmb-rows .wval[data-v="time-${i}"]`);
    if (tm) {
      tm.innerHTML = TRIGGER === "price"
        ? (i === 0 ? `<span class="wpct">bez czekania</span>`
          : `min. <span class="wpct">${(minGaps[i] / 60).toFixed(1)} min</span> przerwy`)
        : `${fmtHM(times[i])} <span class="wpct">+${(gaps[i] / 60).toFixed(1)} min</span>`;
    }
    const pr = $(`#ethmb-rows .wval[data-v="price-${i}"]`);
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
    $("#ethmb-plan").innerHTML = `<span class="muted">Podaj ilość, aby zobaczyć plan.</span>`;
    $("#ethmb-preview").textContent = "";
    return;
  }
  const showTime = TRIGGER !== "price";
  const showOff = TRIGGER !== "time";
  const showGap = minInterval > 0;
  const hasRange = showOff && ($("#ethmb-pmin").value || $("#ethmb-pmax").value);
  const rows = [];
  for (let i = 0; i < n; i++) {
    rows.push(`<tr><td>#${i + 1}</td><td class="mono">${fmtAmt(amounts[i], unit)} ${esc(unit)}</td>
      ${showTime ? `<td class="muted">${fmtTime(times[i])}</td>
                    <td class="mono muted">${(gaps[i] / 60).toFixed(1)} min</td>` : ""}
      ${showGap ? `<td class="mono muted">${i === 0 ? "—"
                    : "≥ " + (minGaps[i] / 60).toFixed(1) + " min"}</td>` : ""}
      ${showOff ? `<td class="mono">${(W.price[i] > 0 ? "+" : "") + (W.price[i] || 0)}%</td>` : ""}
      ${hasRange ? `<td class="mono muted">${effRange(i)}</td>` : ""}</tr>`);
  }
  $("#ethmb-plan").innerHTML = `<table class="tbl"><thead><tr>
      <th>Transza</th><th>Ilość</th>
      ${showTime ? "<th>Planowana na</th><th>Odstęp</th>" : ""}
      ${showGap ? "<th>Min. przerwa</th>" : ""}
      ${showOff ? "<th>±%</th>" : ""}
      ${hasRange ? "<th>Zakres transzy</th>" : ""}</tr></thead>
      <tbody>${rows.join("")}</tbody></table>`;

  const pmin = $("#ethmb-pmin").value, pmax = $("#ethmb-pmax").value;
  const range = (showOff && (pmin || pmax))
    ? ` w zakresie ${pmin || "—"}..${pmax || "—"} ${esc(window.QUOTE_TOKEN)}` : "";
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
  $("#ethmb-preview").innerHTML =
    `${SIDE === "buy" ? "Kupno za" : "Sprzedaż"} <b>${fmtAmt(total, unit)} ${esc(unit)}</b>
     w ${n} transzach${range}, wyzwalacz <b>${trigTxt}</b>${gapTxt},
     tryb <b>${SETTINGS?.dry_run ? "DRY-RUN" : "LIVE"}</b>.${warn}`;
}

// ---------------------------------------------------------------- zlecenia

function sliceDot(s) {
  const cls = { filled: "ok", pending: "pend", skipped: "skip", failed: "fail" }[s.status] || "pend";
  const hash = s.tx_hash || s.tx_signature || "";
  const title = `#${s.idx + 1}: ${s.status} · ${fmt(s.amount, 6)} · plan ${fmtTime(s.scheduled_at)}` +
    (s.price_offset_pct ? ` · offset ${s.price_offset_pct > 0 ? "+" : ""}${s.price_offset_pct}%` : "") +
    (s.executed_price ? ` @ ${fmtPrice(s.executed_price)}` : "") +
    (hash ? ` · ${hash}` : "") +
    (s.error ? ` · ${s.error}` : "");
  // transza z hashem prowadzi wprost na Basescan — inaczej trzeba by
  // przepisywac hash z tooltipa recznie
  return hash
    ? `<a class="sdot ${cls}" href="https://basescan.org/tx/${esc(hash)}"
          target="_blank" rel="noopener" title="${esc(title)}"></a>`
    : `<span class="sdot ${cls}" title="${esc(title)}"></span>`;
}

function orderCard(o) {
  const unit = unitLabel(o);
  const statusCls = { running: "amber", done: "green", cancelled: "muted", failed: "red" }[o.status] || "";
  const filledSlices = o.slices.filter(s => s.status === "filled");
  const avg = filledSlices.length
    ? filledSlices.reduce((a, s) => a + (s.executed_price || 0), 0) / filledSlices.length : null;
  const sigs = filledSlices.filter(s => s.tx_hash || s.tx_signature).length;
  const actions = o.status === "running"
    ? `<button class="btn small danger-ghost" onclick="cancelOrder(${o.id})">✕ Anuluj</button>`
    : `<button class="iconbtn" title="ukryj" onclick="hideOrder(${o.id})">👁</button>
       <button class="iconbtn" title="usuń" onclick="deleteOrder(${o.id})">🗑</button>`;
  return `<div class="order-card">
    <div class="o-head">
      <b>#${o.id} ${o.side === "buy" ? "KUPNO" : "SPRZEDAŻ"} ${esc(window.BASE_TOKEN)}</b>
      <span class="tag ${statusCls}">${esc(o.status)}</span>
      ${o.dry_run ? `<span class="tag">DRY-RUN</span>` : `<span class="tag red">LIVE</span>`}
      <span class="spacer"></span>${actions}
    </div>
    <div class="o-body">
      <div class="slices">${o.slices.map(sliceDot).join("")}</div>
      <div class="o-stats">
        <span>Łącznie: <b>${fmtAmt(o.total_amount, unit)} ${esc(unit)}</b></span>
        <span>Wykonane: <b>${o.filled}/${o.num_slices}</b> (${fmtAmt(o.done_amount, unit)} ${esc(unit)})</span>
        <span>Śr. cena: <b>${avg ? fmtPrice(avg) : "—"}</b></span>
        <span>Zakres: <b>${o.price_min != null ? fmtPrice(o.price_min) : "—"} .. ${o.price_max != null ? fmtPrice(o.price_max) : "—"}</b></span>
        <span>Wyzwalacz: <b>${esc(o.trigger_mode)}</b></span>
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
  const d = await api("/api/eth/multibot" + (SHOW_HIDDEN ? "?hidden=1" : ""));
  if (d.max_slices) {
    window.MAX_SLICES = d.max_slices;
    $("#ethmb-slices").max = d.max_slices;
  }
  $("#ethmb-orders").innerHTML = d.orders.length
    ? d.orders.map(orderCard).join("")
    : `<span class="muted">brak zleceń</span>`;
}

// ---------------------------------------------------------------- bindy

function boot() {
  ["ethmb-amount", "ethmb-start-in", "ethmb-window", "ethmb-pmin", "ethmb-pmax", "ethmb-interval"]
    .forEach(id => $("#" + id).addEventListener("input", refresh));
  document.querySelectorAll("#ethmb-side button").forEach(b =>
    b.addEventListener("click", () => setSide(b.dataset.side)));
  document.querySelectorAll("#ethmb-trigger button").forEach(b =>
    b.addEventListener("click", () => setTrigger(b.dataset.trig)));

  $("#ethmb-slices").addEventListener("input", ensureRows);

  $("#ethmb-rows").addEventListener("input", (e) => {
    const el = e.target.closest("input[type=range]");
    if (!el) return;
    const g = el.dataset.g, i = +el.dataset.i;
    W[g][i] = +el.value;
    resetSkew(g);
    propagate(W, LINK, g, i);        // sprzezone grupy jada za tym suwakiem
    syncSliders();
    refresh();
  });

  $("#ethmb-dist").addEventListener("input", (e) => {
    const el = e.target.closest(".skew");
    if (!el) return;
    const g = el.dataset.skew;
    W[g] = skewWeights(g, +el.value, W[g].length);
    propagateAll(W, LINK, g);
    if (LINK[g]) linkedGroups().forEach(x => { if (x !== g) resetSkew(x); });
    syncSliders();
    refresh();
  });

  $("#ethmb-dist").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-mix],button[data-even],button[data-link]");
    if (!btn) return;
    if (btn.dataset.link) {
      const g = btn.dataset.link;
      LINK[g] = !LINK[g];
      if (LINK[g]) {
        // dolaczana grupa przejmuje uklad suwakow od juz polaczonej
        const master = linkedGroups().find(x => x !== g);
        if (master) { propagateAll(W, LINK, master); syncSliders(); refresh(); }
      }
      applyLinks();
      return;
    }
    const g = btn.dataset.mix || btn.dataset.even;
    W[g] = btn.dataset.mix ? mixWeights(g, W[g].length) : evenWeights(g, W[g].length);
    resetSkew(g);
    propagateAll(W, LINK, g);
    if (LINK[g]) linkedGroups().forEach(x => { if (x !== g) resetSkew(x); });
    syncSliders();
    refresh();
  });

  $("#ethmb-form").addEventListener("submit", onSubmit);
  $("#ethmb-refresh").addEventListener("click", () => { loadPrice(); loadOrders().catch(() => {}); });
  $("#ethmb-show-hidden").addEventListener("change", (e) => {
    SHOW_HIDDEN = e.target.checked;
    loadOrders().catch(err => toast(err.message, "err"));
  });

  window.cancelOrder = async (id) => {
    if (!confirm(`Anulować zlecenie #${id}? Niewykonane transze zostaną pominięte.`)) return;
    try {
      await api(`/api/eth/multibot/${id}/cancel`, { method: "POST" });
      toast("Zlecenie anulowane", "ok");
      await loadOrders();
    } catch (e) { toast(e.message, "err"); }
  };
  window.hideOrder = async (id) => {
    try {
      await api(`/api/eth/multibot/${id}/hide`, { method: "POST", body: { hidden: true } });
      await loadOrders();
    } catch (e) { toast(e.message, "err"); }
  };
  window.deleteOrder = async (id) => {
    if (!confirm(`Usunąć trwale zlecenie #${id}?`)) return;
    try {
      await api(`/api/eth/multibot/${id}`, { method: "DELETE" });
      await loadOrders();
    } catch (e) { toast(e.message, "err"); }
  };

  PLAN_N = curN();
  initDist(PLAN_N);
  renderRows();
  setSide("buy");
  setTrigger("time_price");
  loadSettings().then(refresh).catch(e => toast(e.message, "err"));
  loadPrice();
  loadOrders().catch(e => toast(e.message, "err"));
  // publiczny RPC dlawi przy czestym odpytywaniu, wiec odswiezamy rzadziej
  // niz co 15 s jak w X1
  setInterval(() => { loadPrice(); loadOrders().catch(() => {}); }, 30000);
}

async function onSubmit(e) {
  e.preventDefault();
  const { total, n, start, end, win, minInterval, minGaps } = planData();
  const unit = spendUnit();
  if (!(total > 0)) return toast("Podaj ilość", "err");
  if (sum(minGaps) > win &&
      !confirm(`Minimalne przerwy (${(sum(minGaps) / 60).toFixed(0)} min) nie mieszczą się ` +
               `w oknie ${(win / 60).toFixed(0)} min — ostatnie transze zostaną pominięte. ` +
               `Uruchomić mimo to?`)) return;
  const key_file = $("#ethmb-key").value;
  if (!key_file) return toast("Brak klucza w katalogu wallet_evm/", "err");

  const live = SETTINGS && !SETTINGS.dry_run;
  const msg = `Uruchomić MultiBOT ETH?\n\n${SIDE === "buy" ? "KUPNO za" : "SPRZEDAŻ"} ${total} ${unit}` +
    `\nw ${n} transzach, wyzwalacz: ${TRIGGER}` +
    (live ? "\n\nUWAGA: tryb LIVE — transze wydadzą PRAWDZIWE środki na sieci Base!"
          : "\n\nTryb DRY-RUN — nic nie zostanie wysłane.");
  if (!confirm(msg)) return;

  $("#ethmb-submit").disabled = true;
  try {
    const r = await api("/api/eth/multibot", {
      method: "POST",
      body: {
        side: SIDE, key_file, total_amount: total,
        num_slices: n, window_start: start, window_end: end,
        trigger_mode: TRIGGER,
        weights: W.size.slice(0, n),
        time_weights: W.time.slice(0, n),
        offsets: TRIGGER === "time" ? W.price.map(() => 0) : W.price.slice(0, n),
        min_interval_s: Math.round(minInterval),
        price_min: TRIGGER === "time" ? null : ($("#ethmb-pmin").value || null),
        price_max: TRIGGER === "time" ? null : ($("#ethmb-pmax").value || null),
        note: $("#ethmb-note").value || null,
      },
    });
    toast(`MultiBOT ETH #${r.id} uruchomiony`, "ok");
    $("#ethmb-amount").value = ""; $("#ethmb-note").value = "";
    refresh();
    await loadOrders();
  } catch (err) {
    toast(err.message, "err");
  } finally {
    $("#ethmb-submit").disabled = false;
  }
}

// W przegladarce startujemy UI; w node plik jest tylko biblioteka czystej
// logiki, ktora sprawdza testy (podzial kwoty, odstepy, sprzezenie grup).
if (typeof document !== "undefined") {
  boot();
} else if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    GROUPS, SKEW_SIGN, clamp, sum, neutral, evenWeights, skewWeights, mixWeights,
    toT, fromT, propagate, propagateAll, splitAmount, splitGaps, sliceTimes, minGapsFor,
  };
}
