/* Portfel — portfele (grupy, kolejnosc, zaznaczanie) + salda + proporcje */
"use strict";

const $ = (s) => document.querySelector(s);
const COLORS = ["#4da3ff", "#3fb950", "#d29922", "#b57bff", "#f85149", "#39c5cf"];
let SHOW_HIDDEN = false;

function fmt(x, d = 4) {
  return x == null ? "—" : x.toLocaleString("pl-PL", { maximumFractionDigits: d });
}
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

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

let XNT_USD = null; // kurs XNT->USD (moze byc syntetyczny)

async function load() {
  const data = await api("/api/balances" + (SHOW_HIDDEN ? "?hidden=1" : ""));
  XNT_USD = data.xnt_usd;
  $("#total").textContent = fmt(data.total_xnt, 2);
  $("#total-usd").textContent = XNT_USD
    ? `≈ ${fmt(data.total_xnt * XNT_USD, 2)} USDC.x (orientacyjnie)`
    : "(brak kursu USD)";
  renderWallets(data);
  renderTokens(data);
  drawPie(data.items.filter(it => it.value_xnt > 0));
}

function walletRow(w) {
  const b = w.balances || {};
  const dis = w.hidden ? " hidden-row" : "";
  const bal = (sym) => w.balances ? fmt(b[sym], sym === "USDC.x" ? 3 : 1) : "—";
  return `<tr class="txrow${dis}">
    <td><input type="checkbox" ${w.selected ? "checked" : ""}
        title="zaznacz do zakładki Pary" onchange="toggleSelected(${w.id}, this.checked)"></td>
    <td>
      <b>${esc(w.name)}</b>
      ${w.hidden ? `<span class="badge-hidden">ukryty</span>` : ""}
      <div class="muted mono" style="font-size:11px" title="${esc(w.address)}">
        ${esc(w.address.slice(0, 6))}…${esc(w.address.slice(-6))}</div>
    </td>
    <td class="r mono">${bal("XNT")}</td>
    <td class="r mono">${bal("ANL")}</td>
    <td class="r mono">${bal("XNM")}</td>
    <td class="r mono">${bal("USDC.x")}</td>
    <td class="r mono">${w.balances ? fmt(w.value_xnt, 2) : "—"}</td>
    <td class="r mono">${w.pct != null ? w.pct.toFixed(1) + " %" : "—"}</td>
    <td class="w-actions">
      <button class="iconbtn" title="w górę" onclick="moveWallet(${w.id},'up')">▲</button>
      <button class="iconbtn" title="w dół" onclick="moveWallet(${w.id},'down')">▼</button>
      <button class="iconbtn" title="zmień nazwę / grupę" onclick="editWallet(${w.id}, '${esc(w.name)}', '${esc(w.grp)}')">✎</button>
      <button class="iconbtn" title="${w.hidden ? "pokaż" : "ukryj"}" onclick="toggleHidden(${w.id}, ${w.hidden ? 0 : 1})">${w.hidden ? "↩" : "👁"}</button>
    </td>
  </tr>`;
}

function renderWallets(data) {
  const tbody = $("#tbl-wallets tbody");
  const rows = [];
  const wallets = data.wallets.filter(w => SHOW_HIDDEN || !w.hidden);
  // grupowanie: kolejnosc grup wg pierwszego wystapienia (sort globalny)
  const seen = new Map();
  for (const w of wallets) {
    const key = w.grp || "";
    if (!seen.has(key)) seen.set(key, []);
    seen.get(key).push(w);
  }
  for (const [grp, list] of seen) {
    if (grp) {
      const sum = list.reduce((a, w) => a + (w.value_xnt || 0), 0);
      rows.push(`<tr class="grp-row"><td colspan="6">📁 ${esc(grp)}</td>
        <td class="r mono">${fmt(sum, 2)}</td><td colspan="2"></td></tr>`);
    }
    for (const w of list) rows.push(walletRow(w));
  }
  tbody.innerHTML = rows.join("") || `<tr><td colspan="9" class="muted">brak portfeli — dodaj pierwszy</td></tr>`;
}

function renderTokens(data) {
  const usd = (v) => (XNT_USD && v != null) ? fmt(v * XNT_USD, 2) : "—";
  const rows = data.items.map(it => `
    <tr>
      <td><b>${it.symbol}</b></td>
      <td class="r mono">${fmt(it.amount, 4)}</td>
      <td class="r mono">${it.price_xnt != null ? fmt(it.price_xnt, 7) : "<span class='muted'>brak puli</span>"}</td>
      <td class="r mono">${fmt(it.value_xnt, 3)}</td>
      <td class="r mono">${usd(it.value_xnt)}</td>
      <td class="r mono">${it.pct != null ? it.pct.toFixed(1) + " %" : "—"}</td>
    </tr>`);
  const total_usd = XNT_USD ? fmt(data.total_xnt * XNT_USD, 2) : "—";
  rows.push(`<tr style="border-top:2px solid var(--border)">
      <td><b>RAZEM</b></td><td></td><td></td>
      <td class="r mono"><b>${fmt(data.total_xnt, 3)}</b></td>
      <td class="r mono"><b>${total_usd}</b></td>
      <td class="r mono">100 %</td>
    </tr>`);
  $("#tbl-bal tbody").innerHTML = rows.join("");
}

function drawPie(items) {
  const cv = $("#pie"), ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, cv.width, cv.height);
  const total = items.reduce((a, it) => a + it.value_xnt, 0);
  const legend = $("#legend");
  if (!total) {
    legend.innerHTML = "<span class='muted'>Brak danych do wykresu (brak wycen).</span>";
    return;
  }
  const cx = cv.width / 2, cy = cv.height / 2, R = Math.min(cx, cy) - 10;
  let angle = -Math.PI / 2;
  items.forEach((it, i) => {
    const frac = it.value_xnt / total;
    const a2 = angle + frac * Math.PI * 2;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, R, angle, a2);
    ctx.closePath();
    ctx.fillStyle = COLORS[i % COLORS.length];
    ctx.fill();
    ctx.strokeStyle = "#0d1117";
    ctx.lineWidth = 2;
    ctx.stroke();
    if (frac > 0.04) {
      const mid = (angle + a2) / 2;
      ctx.fillStyle = "#fff";
      ctx.font = "bold 13px Segoe UI";
      ctx.textAlign = "center";
      ctx.fillText(`${it.symbol} ${(frac * 100).toFixed(1)}%`,
        cx + Math.cos(mid) * R * 0.62, cy + Math.sin(mid) * R * 0.62);
    }
    angle = a2;
  });
  legend.innerHTML = items.map((it, i) => `
    <span class="li"><span class="dot" style="background:${COLORS[i % COLORS.length]}"></span>
    ${it.symbol}: ${fmt(it.value_xnt, 2)} XNT (${(it.value_xnt / total * 100).toFixed(1)}%)</span>`).join("");
}

// ---------------------------------------------------------------- akcje

window.toggleSelected = async (id, on) => {
  await api(`/api/wallets/${id}`, { method: "PATCH", body: { selected: on } });
  toast(on ? "Portfel zaznaczony — widoczny w Parach" : "Portfel odznaczony", "ok");
};
window.toggleHidden = async (id, hide) => {
  await api(`/api/wallets/${id}`, { method: "PATCH", body: { hidden: !!hide } });
  await load();
};
window.moveWallet = async (id, dir) => {
  await api(`/api/wallets/${id}/move`, { method: "POST", body: { dir } });
  await load();
};
window.editWallet = async (id, name, grp) => {
  const newName = prompt("Nazwa portfela:", name);
  if (newName === null) return;
  const newGrp = prompt("Grupa (puste = bez grupy):", grp || "");
  if (newGrp === null) return;
  await api(`/api/wallets/${id}`, { method: "PATCH", body: { name: newName, grp: newGrp } });
  await load();
};

$("#form-wallet").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/wallets", {
      method: "POST",
      body: {
        address: $("#w-address").value.trim(),
        name: $("#w-name").value.trim(),
        grp: $("#w-grp").value.trim(),
      },
    });
    $("#w-address").value = ""; $("#w-name").value = ""; $("#w-grp").value = "";
    toast("Portfel dodany", "ok");
    await load();
  } catch (err) { toast(err.message, "err"); }
});

$("#show-hidden").addEventListener("change", (e) => { SHOW_HIDDEN = e.target.checked; load(); });
$("#btn-refresh").addEventListener("click", load);
load().catch(e => toast("Błąd ładowania: " + e.message, "err"));
