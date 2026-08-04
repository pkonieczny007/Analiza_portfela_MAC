/* MultiBOT — zlecenia dzielone na transze */
"use strict";

const $ = (s) => document.querySelector(s);
let SIDE = "buy";
let TRIGGER = "time_price";
let SETTINGS = null;
let SHOW_HIDDEN = false;

function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function fmt(x, d = 4) { return x == null ? "—" : x.toLocaleString("pl-PL", { maximumFractionDigits: d }); }
function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleString("pl-PL",
    { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

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
  time_price: "Transza wykona się, gdy nadejdzie jej czas ORAZ cena mieści się w zakresie.",
  time: "Transza wykona się o zaplanowanej godzinie, niezależnie od ceny.",
  price: "Transza wykona się, gdy tylko cena wejdzie w zakres (czas nie blokuje).",
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
  renderPlan();
}

function setTrigger(t) {
  TRIGGER = t;
  document.querySelectorAll("#mb-trigger button").forEach(b =>
    b.classList.toggle("on", b.dataset.trig === t));
  $("#mb-trigger-help").textContent = TRIGGER_HELP[t];
  $("#mb-range-box").style.display = t === "time" ? "none" : "";
  renderPlan();
}

// ---------------------------------------------------------------- plan

function planData() {
  const total = parseFloat($("#mb-amount").value) || 0;
  const n = Math.max(1, Math.min(window.MAX_SLICES, parseInt($("#mb-slices").value) || 1));
  const startIn = (parseInt($("#mb-start-in").value) || 0) * 60;
  const win = Math.max(1, parseInt($("#mb-window").value) || 1) * 60;
  const now = Math.floor(Date.now() / 1000);
  const start = now + startIn;
  const step = win / n;
  const per = total / n;
  return { total, n, start, end: start + win, step, per };
}

function renderPlan() {
  const { total, n, start, step, per } = planData();
  if (!(total > 0)) {
    $("#mb-plan").innerHTML = `<span class="muted">Podaj ilość, aby zobaczyć plan.</span>`;
    $("#mb-preview").textContent = "";
    return;
  }
  const unit = SIDE === "buy" ? "XNT" : window.TOKEN;
  const rows = [];
  for (let i = 0; i < n; i++) {
    rows.push(`<tr><td>#${i + 1}</td><td class="mono">${fmt(per, 6)} ${unit}</td>
      <td class="muted">${fmtTime(start + step * i)}</td></tr>`);
  }
  $("#mb-plan").innerHTML = `<table class="tbl"><thead><tr>
      <th>Transza</th><th>Ilość</th><th>Planowana na</th></tr></thead>
      <tbody>${rows.join("")}</tbody></table>`;
  const pmin = $("#mb-pmin").value, pmax = $("#mb-pmax").value;
  const range = (TRIGGER !== "time" && (pmin || pmax))
    ? ` w zakresie ${pmin || "—"}..${pmax || "—"} XNT` : "";
  $("#mb-preview").innerHTML =
    `${SIDE === "buy" ? "Kupno za" : "Sprzedaż"} <b>${fmt(total, 6)} ${unit}</b>
     w ${n} transzach${range}, tryb <b>${SETTINGS?.dry_run ? "DRY-RUN" : "LIVE"}</b>.`;
}

["mb-amount", "mb-slices", "mb-start-in", "mb-window", "mb-pmin", "mb-pmax"]
  .forEach(id => $("#" + id).addEventListener("input", renderPlan));
document.querySelectorAll("#mb-side button").forEach(b =>
  b.addEventListener("click", () => setSide(b.dataset.side)));
document.querySelectorAll("#mb-trigger button").forEach(b =>
  b.addEventListener("click", () => setTrigger(b.dataset.trig)));

// ---------------------------------------------------------------- zlecenia

function sliceDot(s) {
  const cls = { filled: "ok", pending: "pend", skipped: "skip", failed: "fail" }[s.status] || "pend";
  const title = `#${s.idx + 1}: ${s.status} · ${fmt(s.amount, 6)}` +
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
        price_min: TRIGGER === "time" ? null : ($("#mb-pmin").value || null),
        price_max: TRIGGER === "time" ? null : ($("#mb-pmax").value || null),
        note: $("#mb-note").value || null,
      },
    });
    toast(`MultiBOT #${r.id} uruchomiony`, "ok");
    $("#mb-amount").value = ""; $("#mb-note").value = "";
    renderPlan();
    await loadOrders();
  } catch (err) { toast(err.message, "err"); }
});

$("#mb-refresh").addEventListener("click", () => { loadPrice(); loadOrders(); });
$("#mb-show-hidden").addEventListener("change", (e) => { SHOW_HIDDEN = e.target.checked; loadOrders(); });

setSide("buy");
setTrigger("time_price");
loadSettings().then(renderPlan).catch(e => toast(e.message, "err"));
loadPrice().catch(() => {});
loadOrders().catch(e => toast(e.message, "err"));
setInterval(() => { loadPrice().catch(() => {}); loadOrders().catch(() => {}); }, 15000);
