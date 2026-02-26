#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from jinja2 import Template


HTML_TEMPLATE = Template(
    r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Spread Monitor Dashboard</title>
  <script src="https://unpkg.com/htmx.org@1.9.12"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen">
  <div class="max-w-[1440px] mx-auto p-3 space-y-3">
    <div id="header-bar" hx-get="/partials/header" hx-trigger="load, every 5s" hx-swap="innerHTML"></div>

    <section class="bg-slate-900 rounded-lg p-3 border border-slate-700">
      <div class="flex items-center justify-between mb-2">
        <h2 class="text-sm font-semibold">Runtime Controls (Hot Reload)</h2>
        <span id="config-save-status" class="text-xs text-slate-400">Idle</span>
      </div>
      <div class="grid grid-cols-8 gap-2 text-xs font-mono">
        <label class="flex flex-col gap-1"><span>threshold</span><input id="cfg-threshold" class="bg-slate-950 border border-slate-700 rounded p-1" /></label>
        <label class="flex flex-col gap-1"><span>kelly_fraction</span><input id="cfg-kelly" class="bg-slate-950 border border-slate-700 rounded p-1" /></label>
        <label class="flex flex-col gap-1"><span>max_paper_order_usd</span><input id="cfg-max-paper" class="bg-slate-950 border border-slate-700 rounded p-1" /></label>
        <label class="flex flex-col gap-1"><span>min_liquidity_usd</span><input id="cfg-min-liq" class="bg-slate-950 border border-slate-700 rounded p-1" /></label>
        <label class="flex flex-col gap-1"><span>min_volume_usd</span><input id="cfg-min-vol" class="bg-slate-950 border border-slate-700 rounded p-1" /></label>
        <label class="flex flex-col gap-1"><span>min_persist_runs</span><input id="cfg-persist" class="bg-slate-950 border border-slate-700 rounded p-1" /></label>
        <label class="flex flex-col gap-1"><span>signal_cooldown_runs</span><input id="cfg-cooldown" class="bg-slate-950 border border-slate-700 rounded p-1" /></label>
        <label class="flex flex-col gap-1"><span>min_improvement_bps</span><input id="cfg-improv-bps" class="bg-slate-950 border border-slate-700 rounded p-1" /></label>
      </div>
      <div class="mt-2 flex items-center gap-2">
        <button id="save-config-btn" class="px-3 py-1 rounded bg-cyan-700 hover:bg-cyan-600 text-xs font-semibold">Save Config</button>
        <span class="text-xs text-slate-400">Writes `strategy.runtime.json` used by `spread_monitor.py --config-file`.</span>
      </div>
    </section>

    <section class="bg-slate-900 rounded-lg p-3 border border-slate-700">
      <div class="flex items-center justify-between mb-2">
        <h2 class="text-sm font-semibold">Paper Positions</h2>
        <div class="flex items-center gap-2 text-xs">
          <select id="positions-filter" class="bg-slate-950 border border-slate-700 rounded p-1 font-mono">
            <option value="all">all</option>
            <option value="winning">winning</option>
            <option value="losing">losing</option>
            <option value="high_edge">entry edge > 2%</option>
          </select>
          <select id="positions-sort" class="bg-slate-950 border border-slate-700 rounded p-1 font-mono">
            <option value="newest">sort: newest</option>
            <option value="pnl_desc">sort: pnl desc</option>
            <option value="pnl_asc">sort: pnl asc</option>
            <option value="age_desc">sort: age desc</option>
          </select>
        </div>
      </div>
      <div class="overflow-auto max-h-[260px]">
        <table class="w-full text-xs font-mono">
          <thead class="sticky top-0 bg-slate-900">
            <tr class="text-slate-300 border-b border-slate-700">
              <th class="text-left p-1">Time</th>
              <th class="text-left p-1">Slug</th>
              <th class="text-left p-1">Side</th>
              <th class="text-right p-1">Entry</th>
              <th class="text-right p-1">Current</th>
              <th class="text-right p-1">Size$</th>
              <th class="text-right p-1">PnL$</th>
              <th class="text-right p-1">PnL%</th>
              <th class="text-right p-1">Age(h)</th>
              <th class="text-right p-1">EntryEdge%</th>
              <th class="text-right p-1">NowEdge%</th>
            </tr>
          </thead>
          <tbody id="positions-body"></tbody>
        </table>
      </div>
    </section>

    <div class="grid grid-cols-5 gap-3">
      <section class="col-span-3 bg-slate-900 rounded-lg p-3 border border-slate-700">
        <div class="flex items-center justify-between mb-2">
          <h2 class="text-sm font-semibold">Opportunity Feed</h2>
          <span class="text-xs text-slate-400">Most recent first</span>
        </div>
        <div class="overflow-auto max-h-[500px]">
          <table class="w-full text-xs">
            <thead class="sticky top-0 bg-slate-900">
              <tr class="text-slate-300 border-b border-slate-700">
                <th class="text-left p-1">Time</th>
                <th class="text-left p-1">Slug</th>
                <th class="text-left p-1">Choice</th>
                <th class="text-right p-1">Strike</th>
                <th class="text-right p-1">Days</th>
                <th class="text-right p-1">Mkt</th>
                <th class="text-right p-1">Model</th>
                <th class="text-right p-1">Gross%</th>
                <th class="text-right p-1">Net%</th>
                <th class="text-right p-1">Order$</th>
                <th class="text-right p-1">Kelly%</th>
                <th class="text-right p-1">SumMid</th>
              </tr>
            </thead>
            <tbody id="opportunity-body" class="font-mono"></tbody>
          </table>
        </div>
      </section>

      <section class="col-span-2 bg-slate-900 rounded-lg p-3 border border-slate-700">
        <div class="flex items-center justify-between mb-2">
          <h2 class="text-sm font-semibold">Signal Log</h2>
          <span class="text-xs text-slate-400">Live events</span>
        </div>
        <div id="signal-feed" class="space-y-2 overflow-auto max-h-[500px] font-mono text-xs"></div>
      </section>
    </div>

    <div class="grid grid-cols-5 gap-3">
      <section class="col-span-3 bg-slate-900 rounded-lg p-3 border border-slate-700">
        <h2 class="text-sm font-semibold mb-2">Edge Distribution (Latest Run)</h2>
        <div class="h-[280px]"><canvas id="edgeChart"></canvas></div>
      </section>

      <section class="col-span-2 bg-slate-900 rounded-lg p-3 border border-slate-700">
        <h2 class="text-sm font-semibold mb-2">Strike Ladder (<= 7 days)</h2>
        <div id="ladder-box" class="space-y-2 text-xs font-mono"></div>
      </section>
    </div>

    <footer id="stats-footer" class="bg-slate-900 rounded-lg p-3 border border-slate-700 font-mono text-xs"></footer>
  </div>

<script>
let edgeChart = null;
let stateCache = null;

function pct(v) { return (v * 100).toFixed(2); }
function n2(v) { return Number(v || 0).toFixed(2); }
function n4(v) { return Number(v || 0).toFixed(4); }
function tsShort(s) {
  if (!s) return "-";
  const d = new Date(s);
  if (isNaN(d)) return s;
  return d.toLocaleTimeString();
}
function slugShort(s) {
  if (!s) return "-";
  return s.length > 38 ? s.slice(0, 38) + "..." : s;
}
function esc(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;', "'":'&#39;'}[m]));
}

async function loadState() {
  const res = await fetch("/api/state", { cache: "no-store" });
  stateCache = await res.json();
  renderAll(stateCache);
}

function renderOpportunity(rows, threshold) {
  const body = document.getElementById("opportunity-body");
  body.innerHTML = rows.map((r) => {
    const over2 = Math.abs((r.sum_mid || 0) - 1.0) > 0.02;
    const over5 = Math.abs((r.sum_mid || 0) - 1.0) > 0.05;
    const hiEdge = (r.net_edge || 0) > 0.05;
    const rowCls = [
      "border-b border-slate-800",
      over5 ? "bg-red-950/40" : "",
      hiEdge ? "border border-amber-400" : ""
    ].join(" ");
    const choiceCls = r.choice === "buy_yes" ? "text-emerald-400" : "text-rose-400";
    const netCls = (r.net_edge || 0) > threshold ? "text-emerald-300 font-bold" : "text-slate-300 font-bold";
    const sumCls = over2 ? "text-orange-300" : "text-slate-300";
    return `
      <tr class="${rowCls}">
        <td class="p-1">${esc(tsShort(r.ts))}</td>
        <td class="p-1"><a class="text-sky-300 hover:underline" target="_blank" href="https://polymarket.com/market/${encodeURIComponent(r.slug || '')}">${esc(slugShort(r.slug))}</a></td>
        <td class="p-1 ${choiceCls}">${esc(r.choice || "-")}</td>
        <td class="p-1 text-right">${n2(r.strike || 0)}</td>
        <td class="p-1 text-right">${n2(r.days_to_expiry || 0)}</td>
        <td class="p-1 text-right">${n4(r.market_price || 0)}</td>
        <td class="p-1 text-right">${n4(r.model_price || 0)}</td>
        <td class="p-1 text-right">${pct(r.gross_edge || 0)}</td>
        <td class="p-1 text-right ${netCls}">${pct(r.net_edge || 0)}</td>
        <td class="p-1 text-right">${n2(r.order_usd || 0)}</td>
        <td class="p-1 text-right">${pct(r.kelly_scaled_fraction || 0)}</td>
        <td class="p-1 text-right ${sumCls}">${n4(r.sum_mid || 0)}</td>
      </tr>
    `;
  }).join("");
}

function renderSignals(rows) {
  const feed = document.getElementById("signal-feed");
  feed.innerHTML = rows.map((r) => {
    const action = r.action || "unknown";
    let badge = "bg-slate-700";
    let label = action.toUpperCase();
    if (action === "paper_trade_signal") { badge = "bg-yellow-700"; label = "SIGNAL"; }
    if (action === "decision") { badge = "bg-blue-700"; label = "DECISION"; }
    if (action === "live_order_submitted") { badge = "bg-red-700"; label = "ORDER"; }
    if (action === "signal_blocked_persistence") { badge = "bg-violet-700"; label = "BLOCK:PERSIST"; }
    if (action === "signal_blocked_cooldown") { badge = "bg-orange-700"; label = "BLOCK:COOLDOWN"; }
    const pass = r.passed_threshold === true ? "✓" : (r.passed_threshold === false ? "✗" : "-");
    return `
      <div class="p-2 rounded border border-slate-700 bg-slate-950/40">
        <div class="flex items-center justify-between">
          <span class="px-2 py-0.5 rounded text-[10px] ${badge}">${label}</span>
          <span class="text-slate-400">${esc(tsShort(r.ts))}</span>
        </div>
        <div class="mt-1">${esc(slugShort(r.slug || "-"))}</div>
        <div class="text-slate-300 mt-1">edge: ${r.net_edge !== null && r.net_edge !== undefined ? pct(r.net_edge) + "%" : "-"}</div>
        <div class="text-slate-300">threshold pass: ${pass}</div>
        <div class="text-slate-400">status: ${esc(r.order_status || "-")}</div>
      </div>
    `;
  }).join("");
}

function renderPositions(rows) {
  const body = document.getElementById("positions-body");
  const filter = document.getElementById("positions-filter")?.value || "all";
  const sortBy = document.getElementById("positions-sort")?.value || "newest";
  let data = [...rows];
  if (filter === "winning") data = data.filter(r => (r.pnl_usd || 0) > 0);
  if (filter === "losing") data = data.filter(r => (r.pnl_usd || 0) < 0);
  if (filter === "high_edge") data = data.filter(r => (r.entry_net_edge || 0) > 0.02);
  if (sortBy === "pnl_desc") data.sort((a, b) => (b.pnl_usd || 0) - (a.pnl_usd || 0));
  if (sortBy === "pnl_asc") data.sort((a, b) => (a.pnl_usd || 0) - (b.pnl_usd || 0));
  if (sortBy === "age_desc") data.sort((a, b) => (b.age_hours || 0) - (a.age_hours || 0));
  if (sortBy === "newest") data.sort((a, b) => String(b.ts || "").localeCompare(String(a.ts || "")));

  body.innerHTML = data.slice(0, 200).map((r) => {
    const pnlCls = (r.pnl_usd || 0) >= 0 ? "text-emerald-300" : "text-rose-300";
    const sideCls = r.choice === "buy_yes" ? "text-emerald-400" : "text-rose-400";
    return `
      <tr class="border-b border-slate-800">
        <td class="p-1">${esc(tsShort(r.ts))}</td>
        <td class="p-1"><a class="text-sky-300 hover:underline" target="_blank" href="https://polymarket.com/market/${encodeURIComponent(r.slug || '')}">${esc(slugShort(r.slug || ""))}</a></td>
        <td class="p-1 ${sideCls}">${esc(r.choice || "-")}</td>
        <td class="p-1 text-right">${n4(r.entry_price || 0)}</td>
        <td class="p-1 text-right">${n4(r.current_price || 0)}</td>
        <td class="p-1 text-right">${n2(r.order_usd || 0)}</td>
        <td class="p-1 text-right ${pnlCls}">${n2(r.pnl_usd || 0)}</td>
        <td class="p-1 text-right ${pnlCls}">${pct(r.pnl_pct || 0)}</td>
        <td class="p-1 text-right">${n2(r.age_hours || 0)}</td>
        <td class="p-1 text-right">${pct(r.entry_net_edge || 0)}</td>
        <td class="p-1 text-right">${pct(r.current_net_edge || 0)}</td>
      </tr>
    `;
  }).join("");
}

function renderConfig(cfg) {
  const get = (k, fallback) => (cfg && cfg[k] !== undefined && cfg[k] !== null) ? cfg[k] : fallback;
  document.getElementById("cfg-threshold").value = get("threshold", "0.012");
  document.getElementById("cfg-kelly").value = get("kelly_fraction", "0.25");
  document.getElementById("cfg-max-paper").value = get("max_paper_order_usd", "100");
  document.getElementById("cfg-min-liq").value = get("min_liquidity_usd", "50000");
  document.getElementById("cfg-min-vol").value = get("min_volume_usd", "100000");
  document.getElementById("cfg-persist").value = get("min_persist_runs", "2");
  document.getElementById("cfg-cooldown").value = get("signal_cooldown_runs", "5");
  document.getElementById("cfg-improv-bps").value = get("min_improvement_bps", "25");
}

async function saveConfig() {
  const status = document.getElementById("config-save-status");
  const payload = {
    threshold: Number(document.getElementById("cfg-threshold").value),
    kelly_fraction: Number(document.getElementById("cfg-kelly").value),
    max_paper_order_usd: Number(document.getElementById("cfg-max-paper").value),
    min_liquidity_usd: Number(document.getElementById("cfg-min-liq").value),
    min_volume_usd: Number(document.getElementById("cfg-min-vol").value),
    min_persist_runs: Number(document.getElementById("cfg-persist").value),
    signal_cooldown_runs: Number(document.getElementById("cfg-cooldown").value),
    min_improvement_bps: Number(document.getElementById("cfg-improv-bps").value)
  };
  status.textContent = "Saving...";
  try {
    const res = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const out = await res.json();
    if (!res.ok || !out.ok) {
      status.textContent = "Save failed";
      return;
    }
    status.textContent = "Saved";
  } catch (_e) {
    status.textContent = "Save failed";
  }
}

function renderChart(rows, threshold) {
  const labels = rows.map(r => slugShort(r.slug || ""));
  const values = rows.map(r => Number((r.net_edge || 0) * 100.0));
  const colors = rows.map(r => (r.net_edge || 0) > threshold ? "rgba(34,197,94,0.85)" : "rgba(148,163,184,0.7)");
  const borderColors = rows.map(r => (r.net_edge || 0) > threshold ? "rgba(34,197,94,1)" : "rgba(148,163,184,1)");
  const ctx = document.getElementById("edgeChart");
  if (!edgeChart) {
    edgeChart = new Chart(ctx, {
      type: "bar",
      data: { labels, datasets: [{ label: "Net Edge %", data: values, backgroundColor: colors, borderColor: borderColors, borderWidth: 1 }] },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: "#cbd5e1" } } },
        scales: {
          x: { ticks: { color: "#cbd5e1" }, grid: { color: "rgba(148,163,184,0.15)" } },
          y: { ticks: { color: "#cbd5e1", font: { size: 10 } }, grid: { color: "rgba(148,163,184,0.08)" } }
        }
      }
    });
  } else {
    edgeChart.data.labels = labels;
    edgeChart.data.datasets[0].data = values;
    edgeChart.data.datasets[0].backgroundColor = colors;
    edgeChart.data.datasets[0].borderColor = borderColors;
    edgeChart.update();
  }
}

function renderLadder(rows, spot) {
  const box = document.getElementById("ladder-box");
  if (!rows.length) {
    box.innerHTML = `<div class="text-slate-400">No markets resolving within 7 days.</div>`;
    return;
  }
  const strikes = rows.map(r => Number(r.strike || 0));
  const minS = Math.min(...strikes);
  const maxS = Math.max(...strikes);
  const denom = Math.max(maxS - minS, 1e-9);
  const spotPct = Math.max(0, Math.min(100, ((spot - minS) / denom) * 100));

  const ruler = `
    <div class="relative h-6 bg-slate-950 rounded border border-slate-700">
      <div class="absolute inset-y-0 w-[2px] bg-cyan-300" style="left:${spotPct}%"></div>
      <div class="absolute left-1 top-1 text-[10px] text-slate-300">min ${n2(minS/1000)}k</div>
      <div class="absolute right-1 top-1 text-[10px] text-slate-300">max ${n2(maxS/1000)}k</div>
      <div class="absolute top-1 -translate-x-1/2 text-[10px] text-cyan-300" style="left:${spotPct}%">spot ${n2(spot/1000)}k</div>
    </div>
  `;

  const rowsHtml = rows.map((r) => {
    const s = Number(r.strike || 0);
    const pctPos = Math.max(0, Math.min(100, ((s - minS) / denom) * 100));
    const near = spot > 0 ? Math.abs(s - spot) / spot <= 0.10 : false;
    return `
      <div class="p-2 rounded border ${near ? "border-emerald-500 bg-emerald-950/20" : "border-slate-700 bg-slate-950/40"}">
        <div class="relative h-4 bg-slate-800 rounded mb-1">
          <div class="absolute inset-y-0 w-[2px] bg-cyan-300/80" style="left:${spotPct}%"></div>
          <div class="absolute inset-y-0 w-[6px] bg-violet-300 rounded" style="left:calc(${pctPos}% - 3px)"></div>
        </div>
        <div class="flex justify-between">
          <span>${esc(slugShort(r.slug))}</span><span>$${n2(s/1000)}k</span>
        </div>
        <div class="text-slate-300">d:${n2(r.days_to_expiry)} yes:${n4(r.yes_mid)} no:${n4(r.no_mid)} modelY:${n4(r.model_yes)} net:${pct(r.net_edge)}%</div>
      </div>
    `;
  }).join("");
  box.innerHTML = ruler + rowsHtml;
}

function renderFooter(stats) {
  const el = document.getElementById("stats-footer");
  el.innerHTML = `
    <div class="grid grid-cols-6 gap-3">
      <div>Total scans: <span class="text-sky-300">${stats.total_scans_run}</span></div>
      <div>Markets eval: <span class="text-sky-300">${stats.total_markets_evaluated}</span></div>
      <div>Paper signals: <span class="text-sky-300">${stats.total_paper_signals}</span></div>
      <div>Paper P&amp;L: <span class="${stats.cumulative_paper_pnl >= 0 ? "text-emerald-300" : "text-rose-300"}">${n2(stats.cumulative_paper_pnl)}</span></div>
      <div>Best edge: <span class="text-emerald-300">${pct(stats.highest_net_edge || 0)}%</span></div>
      <div>Best slug: <span class="text-slate-200">${esc(slugShort(stats.highest_net_edge_slug || "-"))}</span></div>
    </div>
  `;
}

function renderAll(s) {
  const threshold = Number(s?.header?.threshold ?? 0.012);
  renderConfig(s.runtime_config || {});
  renderPositions(s.positions_rows || []);
  renderOpportunity(s.opportunity_rows || [], threshold);
  renderSignals(s.signal_rows || []);
  renderChart(s.chart_rows || [], threshold);
  renderLadder(s.strike_ladder_rows || [], Number(s?.header?.spot || 0));
  renderFooter(s.stats || {});
}

loadState();
setInterval(loadState, 10000);
document.getElementById("save-config-btn").addEventListener("click", saveConfig);
document.getElementById("positions-filter").addEventListener("change", () => renderPositions(stateCache?.positions_rows || []));
document.getElementById("positions-sort").addEventListener("change", () => renderPositions(stateCache?.positions_rows || []));

const source = new EventSource("/stream");
source.onmessage = async (_ev) => { await loadState(); };
source.onerror = () => {};
</script>
</body>
</html>
"""
)


def parse_ts(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


@dataclass
class DashboardState:
    raw_lines: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=200))
    evaluations: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=500))
    signal_rows: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=300))
    current_run_rows: list[dict[str, Any]] = field(default_factory=list)
    latest_eval_by_slug: dict[str, dict[str, Any]] = field(default_factory=dict)
    paper_positions: list[dict[str, Any]] = field(default_factory=list)
    header: dict[str, Any] = field(default_factory=dict)
    stats: dict[str, Any] = field(
        default_factory=lambda: {
            "total_scans_run": 0,
            "total_markets_evaluated": 0,
            "total_paper_signals": 0,
            "highest_net_edge": 0.0,
            "highest_net_edge_slug": "",
        }
    )


state = DashboardState()
state_lock = Lock()
subscribers: set[asyncio.Queue[str]] = set()
log_file_path: Path | None = None
config_file_path: Path | None = None
file_offset = 0


def enrich_evaluation(payload: dict[str, Any]) -> dict[str, Any]:
    choice = payload.get("choice")
    market_price = payload.get("yes_mid") if choice == "buy_yes" else payload.get("no_mid")
    model_price = payload.get("model_yes") if choice == "buy_yes" else payload.get("model_no")
    days_to_expiry = float(payload.get("t_years", 0.0)) * 365.0
    return {
        "ts": payload.get("ts"),
        "slug": payload.get("slug"),
        "choice": choice,
        "strike": float(payload.get("strike", 0.0) or 0.0),
        "days_to_expiry": days_to_expiry,
        "market_price": float(market_price or 0.0),
        "model_price": float(model_price or 0.0),
        "gross_edge": float(payload.get("gross_edge", 0.0) or 0.0),
        "net_edge": float(payload.get("net_edge", 0.0) or 0.0),
        "order_usd": float(payload.get("order_usd", 0.0) or 0.0),
        "kelly_scaled_fraction": float(payload.get("kelly_scaled_fraction", 0.0) or 0.0),
        "yes_mid": float(payload.get("yes_mid", 0.0) or 0.0),
        "no_mid": float(payload.get("no_mid", 0.0) or 0.0),
        "model_yes": float(payload.get("model_yes", 0.0) or 0.0),
        "sum_mid": float(payload.get("sum_mid", 0.0) or 0.0),
    }


def apply_record(rec: dict[str, Any]) -> None:
    action = rec.get("action")
    state.raw_lines.append(rec)

    if action == "run_start":
        state.stats["total_scans_run"] += 1
        state.current_run_rows = []
        ref = rec.get("reference", {}) or {}
        threshold = float(rec.get("threshold", 0.012) or 0.012)
        state.header = {
            "spot": float(ref.get("spot", 0.0) or 0.0),
            "perp": float(ref.get("perp", 0.0) or 0.0),
            "basis_annual": float(ref.get("basis_annual", 0.0) or 0.0),
            "sigma_annual": float(ref.get("sigma_annual", 0.0) or 0.0),
            "last_scan_ts": rec.get("ts"),
            "markets_scanned": int(rec.get("active_markets", 0) or 0),
            "mode": "LIVE" if bool(rec.get("execute_live")) else "PAPER",
            "threshold": threshold,
        }
        return

    if action == "evaluation":
        payload = rec.get("payload", {}) or {}
        row = enrich_evaluation(payload)
        state.evaluations.appendleft(row)
        state.current_run_rows.append(row)
        slug = str(row.get("slug") or "")
        if slug:
            state.latest_eval_by_slug[slug] = row
        state.stats["total_markets_evaluated"] += 1
        if row["net_edge"] > float(state.stats["highest_net_edge"]):
            state.stats["highest_net_edge"] = row["net_edge"]
            state.stats["highest_net_edge_slug"] = row.get("slug") or ""
        return

    if action == "paper_trade_signal":
        payload = rec.get("payload", {}) or {}
        state.stats["total_paper_signals"] += 1
        state.signal_rows.appendleft(
            {
                "ts": rec.get("ts"),
                "action": action,
                "slug": payload.get("slug"),
                "net_edge": float(payload.get("net_edge", 0.0) or 0.0),
                "passed_threshold": True,
                "order_status": None,
            }
        )
        choice = payload.get("choice")
        entry_price = payload.get("yes_mid") if choice == "buy_yes" else payload.get("no_mid")
        state.paper_positions.append(
            {
                "ts": rec.get("ts"),
                "run_id": rec.get("run_id"),
                "slug": payload.get("slug"),
                "choice": choice,
                "entry_price": float(entry_price or 0.0),
                "order_usd": float(payload.get("order_usd", 0.0) or 0.0),
                "entry_net_edge": float(payload.get("net_edge", 0.0) or 0.0),
            }
        )
        return

    if action == "decision":
        net_edge = float(rec.get("net_edge", 0.0) or 0.0)
        threshold = float(rec.get("threshold", 0.012) or 0.012)
        state.signal_rows.appendleft(
            {
                "ts": rec.get("ts"),
                "action": action,
                "slug": rec.get("slug"),
                "net_edge": net_edge,
                "passed_threshold": net_edge >= threshold,
                "order_status": None,
            }
        )
        return

    if action in {"signal_blocked_persistence", "signal_blocked_cooldown"}:
        state.signal_rows.appendleft(
            {
                "ts": rec.get("ts"),
                "action": action,
                "slug": rec.get("slug"),
                "net_edge": float(rec.get("net_edge", 0.0) or 0.0),
                "passed_threshold": False,
                "order_status": None,
            }
        )
        return

    if action == "live_order_submitted":
        response = rec.get("response")
        status = "submitted"
        if isinstance(response, dict) and "error" in response:
            status = "error"
        state.signal_rows.appendleft(
            {
                "ts": rec.get("ts"),
                "action": action,
                "slug": rec.get("slug"),
                "net_edge": None,
                "passed_threshold": None,
                "order_status": status,
            }
        )
        return


def compute_paper_pnl() -> float:
    pnl = 0.0
    for p in state.paper_positions:
        slug = str(p.get("slug") or "")
        row = state.latest_eval_by_slug.get(slug)
        if not row:
            continue
        choice = p.get("choice")
        mark = row.get("yes_mid") if choice == "buy_yes" else row.get("no_mid")
        entry = float(p.get("entry_price", 0.0) or 0.0)
        size = float(p.get("order_usd", 0.0) or 0.0)
        if entry <= 0:
            continue
        shares = size / entry
        pnl += shares * float(mark or 0.0) - size
    return pnl


def build_positions_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in state.paper_positions:
        slug = str(p.get("slug") or "")
        row = state.latest_eval_by_slug.get(slug)
        if not row:
            continue
        choice = p.get("choice")
        mark = float(row.get("yes_mid") if choice == "buy_yes" else row.get("no_mid") or 0.0)
        entry = float(p.get("entry_price", 0.0) or 0.0)
        size = float(p.get("order_usd", 0.0) or 0.0)
        if entry <= 0:
            continue
        shares = size / entry
        current_value = shares * mark
        pnl = current_value - size
        pnl_pct = pnl / size if size > 0 else 0.0
        ts = parse_ts(str(p.get("ts") or ""))
        age_hours = max((datetime.now(timezone.utc) - ts).total_seconds() / 3600.0, 0.0)
        rows.append(
            {
                "ts": p.get("ts"),
                "slug": slug,
                "choice": choice,
                "entry_price": entry,
                "current_price": mark,
                "order_usd": size,
                "current_value": current_value,
                "pnl_usd": pnl,
                "pnl_pct": pnl_pct,
                "age_hours": age_hours,
                "entry_net_edge": float(p.get("entry_net_edge", 0.0) or 0.0),
                "current_net_edge": float(row.get("net_edge", 0.0) or 0.0),
            }
        )
    rows.sort(key=lambda x: x["ts"] or "", reverse=True)
    return rows


def read_runtime_config() -> dict[str, Any]:
    if not config_file_path:
        return {}
    try:
        if not config_file_path.exists():
            return {}
        raw = json.loads(config_file_path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def write_runtime_config(payload: dict[str, Any]) -> None:
    if not config_file_path:
        return
    config_file_path.parent.mkdir(parents=True, exist_ok=True)
    config_file_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def snapshot_state() -> dict[str, Any]:
    with state_lock:
        header = state.header or {
            "spot": 0.0,
            "perp": 0.0,
            "basis_annual": 0.0,
            "sigma_annual": 0.0,
            "last_scan_ts": None,
            "markets_scanned": 0,
            "mode": "PAPER",
            "threshold": 0.012,
        }
        ladder = [r for r in list(state.evaluations) if float(r.get("days_to_expiry", 0.0)) <= 7.0]
        ladder.sort(key=lambda x: float(x.get("strike", 0.0)))
        chart_rows = sorted(state.current_run_rows, key=lambda x: float(x.get("net_edge", 0.0)), reverse=True)[:20]
        positions_rows = build_positions_rows()
        stats = dict(state.stats)
        stats["cumulative_paper_pnl"] = compute_paper_pnl()
        return {
            "header": header,
            "opportunity_rows": list(state.evaluations)[:200],
            "positions_rows": positions_rows[:300],
            "signal_rows": list(state.signal_rows)[:200],
            "chart_rows": chart_rows,
            "strike_ladder_rows": ladder[:50],
            "stats": stats,
            "runtime_config": read_runtime_config(),
            "raw_lines": list(state.raw_lines),
        }


def read_last_json_lines(path: Path, max_lines: int = 200) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    out: list[dict[str, Any]] = []
    for line in text.splitlines()[-max_lines:]:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            if isinstance(rec, dict):
                out.append(rec)
        except json.JSONDecodeError:
            continue
    return out


async def broadcast(msg: str) -> None:
    dead: list[asyncio.Queue[str]] = []
    for q in subscribers:
        try:
            q.put_nowait(msg)
        except Exception:
            dead.append(q)
    for q in dead:
        subscribers.discard(q)


async def tail_file_task() -> None:
    global file_offset
    assert log_file_path is not None
    while True:
        try:
            if not log_file_path.exists():
                await asyncio.sleep(1)
                continue
            with log_file_path.open("r", encoding="utf-8", errors="ignore") as f:
                f.seek(file_offset)
                chunk = f.read()
                file_offset = f.tell()
            if chunk:
                for line in chunk.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    with state_lock:
                        apply_record(rec)
                    await broadcast(json.dumps({"type": "line", "action": rec.get("action"), "ts": rec.get("ts")}))
            else:
                await broadcast(json.dumps({"type": "heartbeat"}))
            await asyncio.sleep(1)
        except Exception:
            await asyncio.sleep(1)


app = FastAPI(title="Spread Monitor Dashboard")


@app.get("/", response_class=HTMLResponse)
async def index(_request: Request) -> HTMLResponse:
    return HTMLResponse(HTML_TEMPLATE.render())


@app.get("/partials/header", response_class=HTMLResponse)
async def partial_header() -> HTMLResponse:
    s = snapshot_state()
    h = s["header"]
    last_scan = parse_ts(h.get("last_scan_ts")).strftime("%Y-%m-%d %H:%M:%S UTC") if h.get("last_scan_ts") else "-"
    mode_cls = "bg-yellow-700 text-yellow-100" if h.get("mode") == "PAPER" else "bg-red-700 text-red-100"
    html = f"""
    <div class="grid grid-cols-8 gap-2 text-xs font-mono">
      <div class="bg-slate-900 border border-slate-700 rounded p-2">Spot<br><span class="text-cyan-300 text-sm">{float(h.get('spot',0.0)):.2f}</span></div>
      <div class="bg-slate-900 border border-slate-700 rounded p-2">Perp<br><span class="text-cyan-300 text-sm">{float(h.get('perp',0.0)):.2f}</span></div>
      <div class="bg-slate-900 border border-slate-700 rounded p-2">Basis Ann%<br><span class="text-cyan-300 text-sm">{float(h.get('basis_annual',0.0))*100:.2f}</span></div>
      <div class="bg-slate-900 border border-slate-700 rounded p-2">Realized Vol%<br><span class="text-cyan-300 text-sm">{float(h.get('sigma_annual',0.0))*100:.2f}</span></div>
      <div class="bg-slate-900 border border-slate-700 rounded p-2 col-span-2">Last Scan<br><span class="text-slate-200 text-sm">{last_scan}</span></div>
      <div class="bg-slate-900 border border-slate-700 rounded p-2">Markets<br><span class="text-cyan-300 text-sm">{int(h.get('markets_scanned',0))}</span></div>
      <div class="bg-slate-900 border border-slate-700 rounded p-2">Mode<br><span class="px-2 py-0.5 rounded {mode_cls}">{h.get('mode','PAPER')}</span></div>
    </div>
    """
    return HTMLResponse(html)


@app.get("/api/state")
async def api_state() -> JSONResponse:
    return JSONResponse(snapshot_state())


@app.get("/api/config")
async def api_get_config() -> JSONResponse:
    return JSONResponse({"config": read_runtime_config()})


@app.post("/api/config")
async def api_set_config(request: Request) -> JSONResponse:
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({"ok": False, "error": "Expected JSON object"}, status_code=400)
    write_runtime_config(data)
    return JSONResponse({"ok": True, "config": read_runtime_config()})


@app.get("/stream")
async def stream() -> StreamingResponse:
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
    subscribers.add(q)

    async def event_generator() -> Any:
        try:
            while True:
                msg = await q.get()
                yield f"data: {msg}\n\n"
        except asyncio.CancelledError:
            return
        finally:
            subscribers.discard(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


def build_initial_state(path: Path) -> None:
    global file_offset
    lines = read_last_json_lines(path, max_lines=200)
    with state_lock:
        for rec in lines:
            apply_record(rec)
    file_offset = path.stat().st_size if path.exists() else 0


def create_startup_task() -> None:
    @app.on_event("startup")
    async def _startup() -> None:
        asyncio.create_task(tail_file_task())


def main() -> None:
    parser = argparse.ArgumentParser(description="Real-time spread monitor dashboard")
    parser.add_argument("--log-file", default="spread_monitor.log.jsonl", help="Path to JSONL log file")
    parser.add_argument("--config-file", default="strategy.runtime.json", help="Runtime config JSON path")
    parser.add_argument("--port", type=int, default=8000, help="Port for dashboard server")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host (default localhost-only for safety)",
    )
    args = parser.parse_args()

    global log_file_path
    global config_file_path
    log_file_path = Path(args.log_file).expanduser().resolve()
    config_file_path = Path(args.config_file).expanduser().resolve()
    build_initial_state(log_file_path)
    create_startup_task()

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
