/* Portfel ETH — salda adresow na Base, sumy grup i proporcje.
   Wzor: static/portfel.js z czesci X1, ale wycena w USD (USDC = 1:1). */
"use strict";

const $ = (s) => document.querySelector(s);
let DATA = null;
let SHOW_HIDDEN = false;

function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function num(x, d = 6) { return x == null ? "—" : Number(x).toLocaleString("pl-PL", { minimumFractionDigits: d, maximumFractionDigits: d }); }
function usd(x) { return x == null ? "—" : Number(x).toLocaleString("pl-PL", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
function pct(x) { return x == null ? "—" : Number(x).toFixed(1) + "%"; }

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

/* Liczba miejsc po przecinku per token: przy ETH szesc miejsc to grosze,
   przy USDC juz szum — dlatego kazdy token ma wlasna precyzje. */
function decFor(sym) { return sym === "ETH" || sym === window.BASE_TOKEN ? 6 : 2; }

// ---------------------------------------------------------------- dane

async function load(refresh = false) {
  const q = (SHOW_HIDDEN ? "?hidden=1" : "?") + (refresh ? "&refresh=1" : "");
  try {
    DATA = await api("/api/eth/portfel/balances" + q);
  } catch (e) {
    toast(e.message, "err");
    return;
  }
  renderWallets();
  renderTokens();
  renderPie();

  $("#ethp-total").textContent = usd(DATA.total_usd);
  $("#ethp-price").textContent = DATA.price
    ? `· 1 ${window.BASE_TOKEN} = ${usd(DATA.price)} USD` + (DATA.cached ? " (z cache)" : "")
    : "· brak kursu";

  const warn = $("#ethp-warn");
  warn.innerHTML = DATA.errors && DATA.errors.length
    ? `Nie udało się pobrać sald: <b>${DATA.errors.map(esc).join(", ")}</b> — publiczny RPC dławi, spróbuj odświeżyć.`
    : "";
  warn.style.display = DATA.errors && DATA.errors.length ? "" : "none";
}

// ---------------------------------------------------------------- tabela portfeli

function walletRow(w) {
  const b = w.balances;
  const cells = DATA.tokens.map(sym =>
    `<td class="r mono">${b ? num(b[sym], decFor(sym)) : "—"}</td>`).join("");
  return `<tr class="${w.hidden ? "row-hidden" : ""}">
    <td><input type="checkbox" ${w.selected ? "checked" : ""}
        title="bierze udział w zakładce ETH Pary"
        onchange="patchWallet(${w.id}, {selected: this.checked})"></td>
    <td>
      <b>${esc(w.name)}</b>
      <div class="mono muted" style="font-size:11px">
        <a href="https://basescan.org/address/${esc(w.address)}" target="_blank"
           rel="noopener">${esc(w.address.slice(0, 10))}…${esc(w.address.slice(-6))}</a>
      </div>
    </td>
    ${cells}
    <td class="r mono">${b ? usd(w.value_usd) : "—"}</td>
    <td class="r mono">${b ? pct(w.pct) : "—"}</td>
    <td class="nowrap">
      <button class="iconbtn" title="w górę" onclick="moveWallet(${w.id},'up')">▲</button>
      <button class="iconbtn" title="w dół" onclick="moveWallet(${w.id},'down')">▼</button>
      <button class="iconbtn" title="${w.hidden ? "pokaż" : "ukryj"}"
              onclick="patchWallet(${w.id}, {hidden: ${w.hidden ? 0 : 1}})">👁</button>
      <button class="iconbtn" title="usuń" onclick="deleteWallet(${w.id})">🗑</button>
    </td></tr>`;
}

/* Naglowek grupy jest zarazem jej suma — tak jak w X1, zeby nie trzeba bylo
   skakac wzrokiem miedzy naglowkiem a osobnym wierszem podsumowania. */
function groupRow(name, rows, span) {
  const sums = DATA.tokens.map(sym => {
    const v = rows.reduce((a, w) => a + (w.balances ? w.balances[sym] : 0), 0);
    return `<td class="r mono">${num(v, decFor(sym))}</td>`;
  }).join("");
  const val = rows.reduce((a, w) => a + (w.value_usd || 0), 0);
  const p = DATA.total_usd ? val / DATA.total_usd * 100 : null;
  return `<tr class="grouprow"><td></td>
    <td><b>${esc(name || "bez grupy")}</b> <span class="muted">(${rows.length})</span></td>
    ${sums}<td class="r mono"><b>${usd(val)}</b></td><td class="r mono">${pct(p)}</td><td></td></tr>`;
}

function renderWallets() {
  const tb = $("#ethp-tbl tbody");
  const shown = DATA.wallets.filter(w => SHOW_HIDDEN || !w.hidden);
  if (!shown.length) {
    tb.innerHTML = `<tr><td colspan="8" class="muted">
      Brak portfeli. Dodaj adres 0x… albo kliknij „⚿ Z moich kluczy".</td></tr>`;
    return;
  }
  const groups = new Map();
  shown.forEach(w => {
    const g = w.grp || "";
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(w);
  });

  let html = "";
  const wiele = groups.size > 1 || (groups.size === 1 && ![...groups.keys()][0] === false);
  for (const [g, rows] of groups) {
    if (groups.size > 1 || g) html += groupRow(g, rows);
    html += rows.map(walletRow).join("");
  }

  // wiersz SUMA po wszystkim, co widac — udzialy zawsze daja 100%
  const totals = DATA.tokens.map(sym => {
    const v = shown.reduce((a, w) => a + (w.balances ? w.balances[sym] : 0), 0);
    return `<td class="r mono"><b>${num(v, decFor(sym))}</b></td>`;
  }).join("");
  html += `<tr class="total-row"><td></td>
    <td><b>SUMA</b>${SHOW_HIDDEN ? ' <span class="muted">(z ukrytymi)</span>' : ""}</td>
    ${totals}<td class="r mono"><b>${usd(DATA.total_usd)}</b></td>
    <td class="r mono"><b>100%</b></td><td></td></tr>`;
  tb.innerHTML = html;
}

function renderTokens() {
  $("#ethp-bal tbody").innerHTML = DATA.items.map(it => `<tr>
    <td><b>${esc(it.symbol)}</b>${it.symbol === "ETH" ? ' <span class="muted">natywny</span>' : ""}</td>
    <td class="r mono">${num(it.amount, decFor(it.symbol))}</td>
    <td class="r mono">${it.price_usd == null ? "—" : usd(it.price_usd)}</td>
    <td class="r mono">${usd(it.value_usd)}</td>
    <td class="r mono">${pct(it.pct)}</td></tr>`).join("");
}

// ---------------------------------------------------------------- wykres

const KOLORY = ["#4da3ff", "#3fb950", "#d29922", "#f85149", "#a371f7", "#39c5cf"];

function renderPie() {
  const cv = $("#ethp-pie");
  const ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, cv.width, cv.height);
  const parts = DATA.items.filter(i => i.value_usd > 0);
  const total = parts.reduce((a, i) => a + i.value_usd, 0);
  if (!total) {
    $("#ethp-legend").innerHTML = `<span class="muted">brak środków do pokazania</span>`;
    return;
  }
  const cx = cv.width / 2, cy = cv.height / 2, r = Math.min(cx, cy) - 10;
  let start = -Math.PI / 2;
  parts.forEach((it, i) => {
    const angle = it.value_usd / total * Math.PI * 2;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, r, start, start + angle);
    ctx.closePath();
    ctx.fillStyle = KOLORY[i % KOLORY.length];
    ctx.fill();
    start += angle;
  });
  $("#ethp-legend").innerHTML = parts.map((it, i) =>
    `<span class="leg"><i style="background:${KOLORY[i % KOLORY.length]}"></i>
     ${esc(it.symbol)} ${pct(it.value_usd / total * 100)}</span>`).join("");
}

// ---------------------------------------------------------------- akcje

window.patchWallet = async (id, body) => {
  try { await api(`/api/eth/wallets/${id}`, { method: "PATCH", body }); await load(true); }
  catch (e) { toast(e.message, "err"); }
};

window.deleteWallet = async (id) => {
  if (!confirm("Usunąć portfel z listy? (środki na łańcuchu zostają nietknięte)")) return;
  try { await api(`/api/eth/wallets/${id}`, { method: "DELETE" }); await load(true); }
  catch (e) { toast(e.message, "err"); }
};

window.moveWallet = async (id, dir) => {
  try { await api(`/api/eth/wallets/${id}/move`, { method: "POST", body: { dir } }); await load(); }
  catch (e) { toast(e.message, "err"); }
};

$("#ethp-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const address = $("#ethp-address").value.trim();
  if (!address) return toast("Podaj adres 0x…", "err");
  try {
    await api("/api/eth/wallets", {
      method: "POST",
      body: { address, name: $("#ethp-name").value.trim(), grp: $("#ethp-grp").value.trim() },
    });
    $("#ethp-address").value = ""; $("#ethp-name").value = "";
    toast("Portfel dodany", "ok");
    await load(true);
  } catch (err) { toast(err.message, "err"); }
});

$("#ethp-from-keys").addEventListener("click", async () => {
  try {
    const r = await api("/api/eth/wallets/from_keys", { method: "POST" });
    toast(r.added.length ? `Dodano: ${r.added.join(", ")}` : "Wszystkie klucze już są na liście",
      r.added.length ? "ok" : "");
    await load(true);
  } catch (e) { toast(e.message, "err"); }
});

$("#ethp-show-hidden").addEventListener("change", (e) => { SHOW_HIDDEN = e.target.checked; load(); });
$("#ethp-refresh").addEventListener("click", () => load(true));

load();
setInterval(() => load(), 60000);
