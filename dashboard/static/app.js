"use strict";

const $ = (id) => document.getElementById(id);
const money = (v) => v == null ? "–" : Math.round(v).toLocaleString("de-CH") + " $";
const pct = (v, sign = true) => v == null ? "–" : (sign && v > 0 ? "+" : "") + (v * 100).toFixed(1) + " %";
const COLORS = { "C live (Paper)": "#3fb950", "SPY halten": "#58a6ff", "C Backtest (gleicher Zeitraum)": "#8b949e" };

function el(tag, attrs = {}, text = "") {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text) e.textContent = text;
  return e;
}

function curvesFrom(data) {
  const rep = data.report;
  if (rep && rep.curves && Object.keys(rep.curves).length) return rep.curves;
  // Fallback: Snapshots (C-Wert und SPY-Kurs), normiert
  const snaps = (data.snapshots || []).filter((s) => s.c_value && s.spy);
  if (snaps.length < 2) return {};
  return {
    "C live (Paper)": { days: snaps.map((s) => s.day), values: snaps.map((s) => s.c_value / snaps[0].c_value) },
    "SPY halten": { days: snaps.map((s) => s.day), values: snaps.map((s) => s.spy / snaps[0].spy) },
  };
}

function drawChart(curves) {
  const svg = $("chart"), legend = $("legend");
  svg.innerHTML = ""; legend.innerHTML = "";
  const names = Object.keys(curves);
  if (!names.length) {
    const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
    Object.entries({ x: 300, y: 120, "text-anchor": "middle", fill: "#8b949e", "font-size": 14 }).forEach(([k, v]) => t.setAttribute(k, v));
    t.textContent = "Noch keine Daten – nach den ersten Läufen.";
    svg.appendChild(t);
    return;
  }
  const all = names.flatMap((n) => curves[n].values);
  let lo = Math.min(...all), hi = Math.max(...all);
  if (hi - lo < 0.01) { lo -= 0.005; hi += 0.005; }
  const W = 600, H = 240, pad = 8;
  const y = (v) => H - pad - (v - lo) / (hi - lo) * (H - 2 * pad);
  const ns = "http://www.w3.org/2000/svg";
  const base = document.createElementNS(ns, "line");
  Object.entries({ x1: 0, x2: W, y1: y(1), y2: y(1), stroke: "#30363d", "stroke-dasharray": "4 4" }).forEach(([k, v]) => base.setAttribute(k, v));
  svg.appendChild(base);
  names.forEach((name, i) => {
    const vals = curves[name].values;
    const color = COLORS[name] || (name.startsWith("Mischung") ? "#bc8cff" : ["#d29922", "#f85149"][i % 2]);
    const pts = vals.map((v, j) => `${(j / Math.max(vals.length - 1, 1) * W).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
    const line = document.createElementNS(ns, "polyline");
    line.setAttribute("points", pts); line.setAttribute("fill", "none"); line.setAttribute("stroke", color);
    line.setAttribute("stroke-width", name.startsWith("C live") ? 2.5 : 1.5);
    line.setAttribute("vector-effect", "non-scaling-stroke");
    if (name.startsWith("C Backtest")) line.setAttribute("stroke-dasharray", "5 4");
    svg.appendChild(line);
    const item = el("span"); const sw = el("i"); sw.style.background = color;
    item.appendChild(sw); item.appendChild(document.createTextNode(`${name} ${pct(vals[vals.length - 1] - 1)}`));
    legend.appendChild(item);
  });
}

function render(data) {
  if (data.missing) {
    $("subtitle").textContent = "Noch kein Lauf – status.json fehlt.";
    return;
  }
  const mode = $("mode");
  mode.textContent = data.mode; mode.className = "badge " + (data.mode === "PAPER" ? "paper" : "dry");
  $("subtitle").textContent = `${data.strategy || "Kandidat C"} · ${data.account_id || "offline"} · ${data.broker}`;
  $("stop-banner").hidden = !data.stop_active;
  $("generated").textContent = data.generated_at ? new Date(data.generated_at * 1000).toLocaleString("de-CH") : "–";

  const acc = data.c_account || {};
  $("c-value").textContent = money(acc.value);
  $("c-sub").textContent = `Budget ${money(acc.budget)}`;
  $("c-inv").textContent = acc.value ? pct(acc.invested / acc.value, false) : "–";
  $("c-cash").textContent = `${money(acc.invested)} investiert · Cash ${money(acc.cash)}`;

  const rep = data.report;
  const live = rep && rep.rows["C live (Paper)"] && rep.rows["C live (Paper)"].USD;
  const spy = rep && rep.rows["SPY halten"] && rep.rows["SPY halten"].USD;
  $("c-ret").textContent = live ? pct(live.total_return) : (acc.budget ? pct(acc.value / acc.budget - 1) : "–");
  $("c-ret").className = "big " + ((live ? live.total_return : 0) >= 0 ? "ok" : "bad");
  $("spy-ret").textContent = spy ? `SPY ${pct(spy.total_return)} · seit ${rep.start}` : "";

  const runs = (data.runs || []).slice().reverse();
  const last = runs[0];
  $("last-run").textContent = last ? new Date(last.ts * 1000).toLocaleString("de-CH", { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "–";
  $("last-run-sub").textContent = last ? `${last.mode} · ${last.ok ? "ok" : "Fehler"} · ${last.fills} Fills` : "";
  const errs = last && last.errors && last.errors.length ? last.errors : [];
  $("error-banner").hidden = !errs.length;
  $("error-banner").textContent = errs.length ? `Letzter Lauf: ${errs[0]}${errs.length > 1 ? ` (+${errs.length - 1})` : ""}` : "";

  drawChart(curvesFrom(data));

  const checks = $("checks"); checks.innerHTML = "";
  if (rep) {
    $("test-state").textContent = rep.finished ? (rep.passed ? "– BESTANDEN" : "– durchgefallen") : `– läuft, Monat ${rep.months_done + 1} von 6`;
    rep.checks.forEach((c) => {
      const li = el("li");
      li.appendChild(el("span", { class: c.ok ? "ok" : "bad" }, c.ok ? "✓" : "✗"));
      li.appendChild(el("span", {}, `${c.code} ${c.text} – ${c.detail}`));
      checks.appendChild(li);
    });
    if (rep.estimated_fills) checks.appendChild(el("li", { class: "muted" }, `${rep.estimated_fills} Fill(s) mit geschätztem Tag (siehe ROADMAP).`));
  } else {
    checks.appendChild(el("li", { class: "muted" }, "Noch kein Report (ab dem 2. Handelstag)."));
  }

  const tbody = $("positions"); tbody.innerHTML = "";
  const systems = data.systems || [];
  const held = systems.filter((s) => s.bot_qty > 0 || s.entry_pending || s.exit_pending);
  $("pos-count").textContent = `${held.length} von ${systems.length} Systemen`;
  held.forEach((s) => {
    const tr = el("tr");
    const sym = el("td", {}, s.symbol);
    if (s.exit_pending) sym.appendChild(el("span", { class: "tag" }, "Verkauf"));
    if (s.entry_pending) sym.appendChild(el("span", { class: "tag" }, "Einstieg"));
    if (s.broker_qty > s.bot_qty) sym.appendChild(el("span", { class: "tag" }, `+${s.broker_qty - s.bot_qty} fremd`));
    tr.appendChild(sym);
    tr.appendChild(el("td", { class: "num" }, String(s.bot_qty)));
    tr.appendChild(el("td", { class: "num" }, s.avg ? s.avg.toFixed(2) : "–"));
    tr.appendChild(el("td", { class: "num" }, s.price ? s.price.toFixed(2) : "–"));
    tr.appendChild(el("td", { class: "num " + (s.pnl >= 0 ? "ok" : "bad") }, money(s.pnl)));
    tr.appendChild(el("td", {}, `${s.filled_levels} gefüllt · ${s.open_levels.length} offen`));
    tbody.appendChild(tr);
  });
  const nt = systems.filter((s) => s.not_tradable).map((s) => s.symbol);
  const closed = systems.filter((s) => s.closed).map((s) => `${s.symbol} (${s.closed})`);
  $("excluded").textContent = [nt.length ? `Ohne Handelsberechtigung: ${nt.join(", ")}` : "", closed.length ? `Geschlossen: ${closed.join(", ")}` : ""]
    .filter(Boolean).join(" · ");

  const ev = $("events"); ev.innerHTML = "";
  const events = (data.events || []).slice().reverse().slice(0, 40);
  if (!events.length) ev.appendChild(el("li", { class: "muted" }, "Noch keine Ereignisse."));
  events.forEach((e) => {
    const li = el("li");
    const cls = e.level === "error" ? "bad" : e.level === "warn" ? "muted" : "ok";
    li.appendChild(el("span", { class: cls }, e.level === "error" ? "●" : e.level === "warn" ? "▲" : "●"));
    li.appendChild(el("span", {}, `${new Date(e.ts * 1000).toLocaleString("de-CH", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })} · ${e.message}`));
    ev.appendChild(li);
  });

  const ul = $("runs"); ul.innerHTML = "";
  runs.slice(0, 10).forEach((r) => {
    const li = el("li");
    li.appendChild(el("span", { class: r.ok ? "ok" : "bad" }, r.ok ? "●" : "●"));
    li.appendChild(el("span", {}, `${new Date(r.ts * 1000).toLocaleString("de-CH")} · ${r.mode} · ${r.fills} Fills, ${r.orders} Orders` +
      (r.errors && r.errors.length ? ` · ${r.errors[0]}` : "")));
    ul.appendChild(li);
  });
}

async function load() {
  try {
    const res = await fetch("/api/status", { cache: "no-store" });
    render(await res.json());
  } catch (e) {
    $("error-banner").hidden = false;
    $("error-banner").textContent = "Server nicht erreichbar (Tailscale an?).";
  }
}

$("stop-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (!confirm("Wirklich STOP-ALL? Der Bot handelt ab dem nächsten Lauf nicht mehr.")) return;
  const msg = $("stop-msg");
  try {
    const res = await fetch("/api/stop", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pin: $("pin").value }) });
    const body = await res.json();
    msg.textContent = res.ok ? body.message : (body.detail || "Fehler");
    msg.className = "small " + (res.ok ? "ok" : "bad");
    $("pin").value = "";
    load();
  } catch (e) {
    msg.textContent = "Server nicht erreichbar."; msg.className = "small bad";
  }
});

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js");
load();
setInterval(load, 60000);
