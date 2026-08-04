/* Portfel — salda i proporcje (pkt 2, wersja podstawowa) */
"use strict";

const $ = (s) => document.querySelector(s);
const COLORS = ["#4da3ff", "#3fb950", "#d29922", "#b57bff", "#f85149", "#39c5cf"];

function fmt(x, d = 4) {
  return x == null ? "—" : x.toLocaleString("pl-PL", { maximumFractionDigits: d });
}

async function load() {
  const r = await fetch("/api/balances");
  const data = await r.json();

  $("#total").textContent = fmt(data.total_xnt, 2);

  const tbody = $("#tbl-bal tbody");
  tbody.innerHTML = data.items.map(it => `
    <tr>
      <td><b>${it.symbol}</b></td>
      <td class="r mono">${fmt(it.amount, 4)}</td>
      <td class="r mono">${it.price_xnt != null ? fmt(it.price_xnt, 7) : "<span class='muted'>brak puli</span>"}</td>
      <td class="r mono">${fmt(it.value_xnt, 3)}</td>
      <td class="r mono">${it.pct != null ? it.pct.toFixed(1) + " %" : "—"}</td>
    </tr>`).join("");

  drawPie(data.items.filter(it => it.value_xnt > 0));
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
    // etykieta procentowa na wykresie
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

$("#btn-refresh").addEventListener("click", load);
load();
