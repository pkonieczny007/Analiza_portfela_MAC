/* Zakladka ETH — swap WETH/USDC na Uniswap v3 (Base).
   Dwustopniowe zatwierdzanie jak po stronie X1: pierwszy klik to wycena
   (POST bez confirm), dopiero drugi wysyla zlecenie. */
"use strict";

const $ = (s) => document.querySelector(s);
let SIDE = "buy";
let SETTINGS = null;
let PENDING = null;          // wycena czekajaca na potwierdzenie
const LOG = [];

function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function fmt(x, d = 6) { return x == null ? "—" : Number(x).toLocaleString("pl-PL", { maximumFractionDigits: d }); }

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

function spendToken() { return SIDE === "buy" ? window.QUOTE_TOKEN : window.BASE_TOKEN; }
function getToken() { return SIDE === "buy" ? window.BASE_TOKEN : window.QUOTE_TOKEN; }

// ---------------------------------------------------------------- init

async function loadSettings() {
  SETTINGS = await api("/api/eth/settings");
  const badge = $("#eth-mode");
  badge.textContent = SETTINGS.dry_run ? "DRY-RUN" : "LIVE — realne środki";
  badge.className = "mode-badge " + (SETTINGS.dry_run ? "dry" : "live");
  $("#eth-dry").checked = SETTINGS.dry_run;
  if (!$("#eth-slippage").value) $("#eth-slippage").value = SETTINGS.slippage_bps;

  $("#eth-key").innerHTML = SETTINGS.keys.length
    ? SETTINGS.keys.map(k => `<option value="${esc(k.filename)}">${esc(k.name)} — ${esc(k.address.slice(0, 6))}…${esc(k.address.slice(-4))}</option>`).join("")
    : `<option value="">brak kluczy w wallet_evm/</option>`;

  const problems = [];
  if (!SETTINGS.available) problems.push("Brak biblioteki <b>eth-account</b> — <code>pip install eth-account</code>.");
  if (!SETTINGS.keys.length) problems.push("Brak kluczy — wrzuć plik do katalogu <code>wallet_evm/</code>.");
  if (SETTINGS.rpc_public) problems.push("Publiczny RPC Base — dławi przy częstych zapytaniach (HTTP 429). Do handlu ustaw <code>EVM_RPC_URL</code>.");
  const warn = $("#eth-warn");
  warn.innerHTML = problems.join("<br>");
  warn.style.display = problems.length ? "" : "none";
  $("#eth-submit").disabled = !SETTINGS.available || !SETTINGS.keys.length;
  loadBalances();
}

async function loadPrice() {
  try {
    const p = await api("/api/eth/price");
    $("#eth-price").textContent = p.price
      ? p.price.toLocaleString("pl-PL", { maximumFractionDigits: 2 }) + " " + window.QUOTE_TOKEN
      : "—";
  } catch (e) { $("#eth-price").textContent = "—"; }
}

async function loadBalances() {
  const key = $("#eth-key").value;
  if (!key) { $("#eth-balances").textContent = "wybierz klucz"; return; }
  try {
    const b = await api("/api/eth/balances?key_file=" + encodeURIComponent(key));
    $("#eth-balances").innerHTML = `<table class="tbl"><tbody>
      <tr><td class="muted">adres</td><td class="mono">${esc(b.address)}</td></tr>
      <tr><td class="muted">ETH (natywny)</td><td class="mono">${fmt(b.ETH)}</td></tr>
      <tr><td class="muted">${esc(window.BASE_TOKEN)}</td><td class="mono">${fmt(b[window.BASE_TOKEN])}</td></tr>
      <tr><td class="muted">${esc(window.QUOTE_TOKEN)}</td><td class="mono">${fmt(b[window.QUOTE_TOKEN], 2)}</td></tr>
      </tbody></table>`;
  } catch (e) { $("#eth-balances").innerHTML = `<span class="muted">${esc(e.message)}</span>`; }
}

function setSide(side) {
  SIDE = side;
  document.querySelectorAll("#eth-side button").forEach(b =>
    b.classList.toggle("on", b.dataset.side === side));
  $("#eth-unit").textContent = spendToken();
  resetPending();
}

// ---------------------------------------------------------------- wycena

function resetPending() {
  PENDING = null;
  $("#eth-submit").textContent = "Wyceń";
  $("#eth-submit").classList.remove("danger");
}

async function refreshTiers() {
  const amount = parseFloat($("#eth-amount").value) || 0;
  if (!(amount > 0)) { $("#eth-tiers").textContent = "podaj ilość, aby porównać pule"; return; }
  try {
    const q = await api("/api/eth/quote", { method: "POST", body: { side: SIDE, amount } });
    const rows = q.alternatives.map(a => `<tr${a.fee === q.fee ? ' class="best"' : ""}>
      <td>${(a.fee / 10000).toFixed(2)}%</td>
      <td class="r mono">${fmt(a.amount_out, 6)} ${esc(q.token_out)}</td>
      <td class="muted">${a.fee === q.fee ? "← wybrana" : ""}</td></tr>`).join("");
    $("#eth-tiers").innerHTML = `<table class="tbl"><thead><tr>
        <th>Opłata puli</th><th class="r">Dostaniesz</th><th></th></tr></thead>
        <tbody>${rows}</tbody></table>
      <div class="muted" style="margin-top:6px">
        Minimum przy poślizgu ${q.slippage_bps} bps: <b>${fmt(q.min_out, 6)} ${esc(q.token_out)}</b></div>`;
  } catch (e) { $("#eth-tiers").innerHTML = `<span class="muted">${esc(e.message)}</span>`; }
}

let tiersTimer = null;
$("#eth-amount").addEventListener("input", () => {
  resetPending();
  clearTimeout(tiersTimer);
  tiersTimer = setTimeout(refreshTiers, 600);   // publiczny RPC nie lubi kazdego klawisza
});

document.querySelectorAll("#eth-side button").forEach(b =>
  b.addEventListener("click", () => setSide(b.dataset.side)));
$("#eth-key").addEventListener("change", loadBalances);

$("#eth-dry").addEventListener("change", async (e) => {
  const dry = e.target.checked;
  if (!dry && !confirm("Wyłączyć DRY-RUN?\n\nOd tej chwili zlecenia w zakładce ETH wydają PRAWDZIWE środki na sieci Base.")) {
    e.target.checked = true;
    return;
  }
  await api("/api/eth/settings", { method: "POST", body: { dry_run: dry } });
  await loadSettings();
});

$("#eth-slippage").addEventListener("change", async (e) => {
  try {
    await api("/api/eth/settings", { method: "POST", body: { slippage_bps: e.target.value } });
    toast("Poślizg zapisany", "ok");
    refreshTiers();
  } catch (err) { toast(err.message, "err"); }
});

// ---------------------------------------------------------------- zlecenie

$("#eth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const amount = parseFloat($("#eth-amount").value) || 0;
  const key_file = $("#eth-key").value;
  if (!(amount > 0)) return toast("Podaj ilość", "err");
  if (!key_file) return toast("Brak klucza w katalogu wallet_evm/", "err");

  // krok 1: wycena
  if (!PENDING) {
    try {
      const p = await api("/api/eth/trade", { method: "POST", body: { side: SIDE, amount, key_file } });
      PENDING = { ...p, amount, key_file };
      $("#eth-preview").innerHTML =
        `${SIDE === "buy" ? "Kupno za" : "Sprzedaż"} <b>${fmt(amount)} ${esc(p.token_in)}</b>
         → oczekiwane <b>${fmt(p.expected_out)} ${esc(p.token_out)}</b>
         (pula ${(p.fee_tier / 10000).toFixed(2)}%, minimum ${fmt(p.min_out)} przy ${p.slippage_bps} bps).
         Tryb <b>${p.dry_run ? "DRY-RUN" : "LIVE"}</b>. Kliknij ponownie, aby ${p.dry_run ? "złożyć próbnie" : "WYSŁAĆ"}.`;
      $("#eth-submit").textContent = p.dry_run ? "Złóż próbnie (DRY-RUN)" : "WYŚLIJ ZLECENIE";
      if (!p.dry_run) $("#eth-submit").classList.add("danger");
    } catch (err) { toast(err.message, "err"); }
    return;
  }

  // krok 2: potwierdzenie
  if (!PENDING.dry_run && !confirm(
    `TRYB LIVE\n\n${SIDE === "buy" ? "Kupno za" : "Sprzedaż"} ${amount} ${PENDING.token_in}\n` +
    `Oczekiwane: ${PENDING.expected_out} ${PENDING.token_out}\n` +
    `Minimum: ${PENDING.min_out}\n\nWysłać na sieć?`)) return;

  $("#eth-submit").disabled = true;
  try {
    const r = await api("/api/eth/trade", {
      method: "POST",
      body: { side: SIDE, amount, key_file, confirm: true },
    });
    LOG.unshift(r);
    renderLog();
    toast(r.dry_run ? "DRY-RUN: zlecenie złożone próbnie" : `Wysłane: ${r.tx_hash}`, "ok");
    $("#eth-amount").value = "";
    resetPending();
    $("#eth-preview").textContent = "";
    loadBalances();
  } catch (err) {
    toast(err.message, "err");
  } finally {
    $("#eth-submit").disabled = false;
  }
});

function renderLog() {
  if (!LOG.length) { $("#eth-log").textContent = "brak"; return; }
  $("#eth-log").innerHTML = `<table class="tbl"><thead><tr>
      <th>Tryb</th><th>Kierunek</th><th class="r">Wydane</th><th class="r">Oczekiwane</th>
      <th class="r">Minimum</th><th>Pula</th><th>Transakcja</th></tr></thead><tbody>` +
    LOG.map(r => `<tr>
      <td>${r.dry_run ? '<span class="tag">DRY-RUN</span>' : '<span class="tag red">LIVE</span>'}</td>
      <td>${r.side === "buy" ? "KUPNO" : "SPRZEDAŻ"}</td>
      <td class="r mono">${fmt(r.amount_in)}</td>
      <td class="r mono">${fmt(r.expected_out)}</td>
      <td class="r mono">${fmt(r.min_out)}</td>
      <td>${(r.fee_tier / 10000).toFixed(2)}%</td>
      <td class="mono">${r.tx_hash
        ? `<a href="https://basescan.org/tx/${esc(r.tx_hash)}" target="_blank" rel="noopener">${esc(r.tx_hash.slice(0, 10))}…</a>`
        : `<span class="muted">${esc(r.note || "")}</span>`}</td></tr>`).join("") +
    `</tbody></table>`;
}

$("#eth-refresh").addEventListener("click", () => { loadPrice(); loadBalances(); refreshTiers(); });

setSide("buy");
loadSettings().catch(e => toast(e.message, "err"));
loadPrice();
setInterval(loadPrice, 30000);
