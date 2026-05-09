"""Generate a self-contained focused HTML performance report.

Usage:
    ./venv/bin/python scripts/analysis/deep_dive_html.py [--start 2026-02-01] [--out PATH]

Produces a single HTML file with four focused views:
  1. AI council (PM) verdict      -> 4w stock performance
  2. Deep Research verdict        -> 4w stock performance
  3. AI council R/R ratio bucket  -> 4w stock performance
  4. Deep Research R/R bucket     -> 4w stock performance (where available)

Plus a filterable/sortable per-decision explorer table at the bottom.
Self-contained — Chart.js from CDN, just open in a browser.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app.services.analytics.payload import build_payload  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("deep_dive_html")


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>StockDrop Performance — Focused</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
    :root {
        --bg: #0f172a;
        --panel: #1e293b;
        --border: #334155;
        --text: #e2e8f0;
        --muted: #94a3b8;
        --accent: #60a5fa;
        --green: #22c55e;
        --red: #ef4444;
    }
    * { box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, sans-serif;
           margin: 0; padding: 24px; background: var(--bg); color: var(--text);
           font-size: 14px; line-height: 1.5; }
    h1 { font-size: 1.75rem; margin: 0 0 4px 0; }
    h2 { font-size: 1.05rem; margin: 28px 0 12px 0; padding-bottom: 6px;
         border-bottom: 1px solid var(--border); color: var(--muted);
         text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600; }
    h3 { font-size: 0.95rem; margin: 0; font-weight: 600; }
    .muted { color: var(--muted); font-size: 0.85rem; }
    .container { max-width: 1280px; margin: 0 auto; }
    .grid { display: grid; gap: 14px; }
    .grid-2 { grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); }
    .card { background: var(--panel); border: 1px solid var(--border);
            border-radius: 8px; padding: 16px; }
    .card-header { padding-bottom: 10px; border-bottom: 1px solid var(--border);
                   margin-bottom: 12px; }
    .card-header .sub { color: var(--muted); font-size: 0.8rem; margin-top: 3px; }
    .chart-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .chart-box { height: 240px; position: relative; }
    .chart-box.tall { height: 380px; }
    table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
    th, td { padding: 7px 9px; text-align: left; border-bottom: 1px solid var(--border); }
    th { color: var(--muted); font-weight: 500; cursor: pointer; user-select: none;
         white-space: nowrap; }
    th:hover { color: var(--text); }
    th.sort-asc::after { content: " \\25B2"; }
    th.sort-desc::after { content: " \\25BC"; }
    td.num { text-align: right; font-variant-numeric: tabular-nums; }
    td.pos { color: var(--green); }
    td.neg { color: var(--red); }
    .ticker { color: var(--accent); font-weight: 600; text-decoration: none; }
    .ticker:hover { text-decoration: underline; }
    .pill { display: inline-block; padding: 1px 8px; border-radius: 10px;
            font-size: 0.72rem; font-weight: 600; }
    .pill.enter_now { background: #15803d; color: #dcfce7; }
    .pill.enter_limit { background: #1e40af; color: #dbeafe; }
    .pill.avoid { background: #991b1b; color: #fee2e2; }
    .pill.neutral { background: #475569; color: #f1f5f9; }
    input, select { background: var(--bg); color: var(--text);
                    border: 1px solid var(--border); border-radius: 4px; padding: 6px 10px;
                    font-size: 0.85rem; }
    .filters { display: flex; gap: 10px; flex-wrap: wrap; align-items: center;
               justify-content: space-between; }
    .scroll-table { max-height: 540px; overflow-y: auto; }
    .empty { color: var(--muted); padding: 30px; text-align: center; }
    .badge { display: inline-block; padding: 1px 8px; border-radius: 10px;
             font-size: 0.72rem; font-weight: 600; }
    .badge.sig { background: #15803d; color: #dcfce7; }
    .badge.ns  { background: #475569; color: #e2e8f0; }
    .badge.ns  { background: #334155; color: #94a3b8; }
    .toggle-bar { display: inline-flex; gap: 4px; }
    .toggle-bar button { background: var(--bg); color: var(--text);
                        border: 1px solid var(--border); padding: 4px 10px;
                        border-radius: 4px; cursor: pointer; font-size: 0.78rem; }
    .toggle-bar button.active { background: var(--accent); color: #0b1220;
                               border-color: var(--accent); }
    .stat-row { font-size: 0.85rem; color: var(--muted); padding: 6px 0; }
    .stat-row strong { color: var(--text); margin-right: 6px; }
</style>
</head>
<body>
<div class="container">
    <h1>StockDrop Performance — Focused View</h1>
    <div class="muted" id="meta"></div>

    <h2>Verdict → 4-week return</h2>
    <div class="grid grid-2">
        <div class="card" id="card-pm">
            <div class="card-header">
                <h3>AI council (PM) verdict</h3>
                <div class="sub">Pre-Deep-Research recommendation, by normalized intent</div>
            </div>
            <div class="chart-grid">
                <div><div class="muted" style="text-align:center; font-size: 0.75rem;">Win rate</div>
                     <div class="chart-box"><canvas id="pmWinChart"></canvas></div></div>
                <div><div class="muted" style="text-align:center; font-size: 0.75rem;">Avg return</div>
                     <div class="chart-box"><canvas id="pmRetChart"></canvas></div></div>
            </div>
        </div>
        <div class="card" id="card-dr">
            <div class="card-header">
                <h3>Deep Research verdict</h3>
                <div class="sub">DR's own verdict (where set)</div>
            </div>
            <div class="chart-grid">
                <div><div class="muted" style="text-align:center; font-size: 0.75rem;">Win rate</div>
                     <div class="chart-box"><canvas id="drWinChart"></canvas></div></div>
                <div><div class="muted" style="text-align:center; font-size: 0.75rem;">Avg return</div>
                     <div class="chart-box"><canvas id="drRetChart"></canvas></div></div>
            </div>
        </div>
    </div>

    <h2>R/R ratio → 4-week return</h2>
    <div class="grid grid-2">
        <div class="card" id="card-pmrr">
            <div class="card-header">
                <h3>AI council R/R ratio</h3>
                <div class="sub">PM-supplied risk_reward_ratio, bucketed</div>
            </div>
            <div class="chart-grid">
                <div><div class="muted" style="text-align:center; font-size: 0.75rem;">Win rate</div>
                     <div class="chart-box"><canvas id="pmRRWinChart"></canvas></div></div>
                <div><div class="muted" style="text-align:center; font-size: 0.75rem;">Avg return</div>
                     <div class="chart-box"><canvas id="pmRRRetChart"></canvas></div></div>
            </div>
        </div>
        <div class="card" id="card-drrr">
            <div class="card-header">
                <h3>Deep Research R/R ratio</h3>
                <div class="sub">DR-supplied rr_ratio, bucketed (where available)</div>
            </div>
            <div class="chart-grid">
                <div><div class="muted" style="text-align:center; font-size: 0.75rem;">Win rate</div>
                     <div class="chart-box"><canvas id="drRRWinChart"></canvas></div></div>
                <div><div class="muted" style="text-align:center; font-size: 0.75rem;">Avg return</div>
                     <div class="chart-box"><canvas id="drRRRetChart"></canvas></div></div>
            </div>
        </div>
    </div>

    <h2>Performance over time since signal — vs S&amp;P 500</h2>
    <div style="margin-bottom: 10px;">
        <div class="toggle-bar" id="tsModeToggle">
            <button data-mode="absolute" class="active">Absolute return</button>
            <button data-mode="alpha">Excess vs SPY</button>
        </div>
        <span class="muted" style="margin-left: 12px; font-size: 0.78rem;">
            SPY median over the same calendar windows is shown as a dashed line.
        </span>
    </div>
    <div class="grid grid-2">
        <div class="card">
            <div class="card-header">
                <h3>Median return path by AI council verdict</h3>
                <div class="sub">Daily close vs decision price, normalized to 0% at day 0</div>
            </div>
            <div class="chart-box tall"><canvas id="tsIntentChart"></canvas></div>
        </div>
        <div class="card">
            <div class="card-header">
                <h3>Median return path by Deep Research verdict</h3>
                <div class="sub">Same view, grouped by DR verdict where set</div>
            </div>
            <div class="chart-box tall"><canvas id="tsDrChart"></canvas></div>
        </div>
    </div>

    <div class="card">
        <div class="card-header">
            <h3>BUY signal trajectories</h3>
            <div class="sub">
                Each grey line is one ENTER_NOW or ENTER_LIMIT decision; the bold lines are
                the per-intent medians.
            </div>
        </div>
        <div class="chart-box tall"><canvas id="tsSpaghetti"></canvas></div>
    </div>

    <h2>Statistical significance — return_4w differences between groups</h2>
    <div class="grid grid-2">
        <div class="card">
            <div class="card-header">
                <h3>AI council verdict (intent)</h3>
                <div class="sub">
                    Pairwise Welch t-test (unequal variance) and Mann-Whitney U;
                    p-values FDR-adjusted (Benjamini-Hochberg).
                </div>
            </div>
            <div class="scroll-table">
                <table id="sigIntentTable">
                    <thead><tr>
                        <th>Group A</th><th>Group B</th>
                        <th class="num">N₁ / N₂</th>
                        <th class="num">Δ mean</th>
                        <th class="num">Cohen's d</th>
                        <th class="num">Welch p (FDR)</th>
                        <th class="num">MWU p (FDR)</th>
                        <th>Sig.</th>
                    </tr></thead>
                    <tbody></tbody>
                </table>
            </div>
        </div>
        <div class="card">
            <div class="card-header">
                <h3>Deep Research verdict</h3>
                <div class="sub">Same comparison applied to DR groups (smaller n).</div>
            </div>
            <div class="scroll-table">
                <table id="sigDrTable">
                    <thead><tr>
                        <th>Group A</th><th>Group B</th>
                        <th class="num">N₁ / N₂</th>
                        <th class="num">Δ mean</th>
                        <th class="num">Cohen's d</th>
                        <th class="num">Welch p (FDR)</th>
                        <th class="num">MWU p (FDR)</th>
                        <th>Sig.</th>
                    </tr></thead>
                    <tbody></tbody>
                </table>
            </div>
        </div>
    </div>

    <h2>R/R ratio vs realized 4w return — correlation</h2>
    <div class="grid grid-2">
        <div class="card">
            <div class="card-header">
                <h3>AI council R/R (risk_reward_ratio)</h3>
                <div id="corrPmStats" class="sub"></div>
            </div>
            <div class="chart-box tall"><canvas id="corrPmChart"></canvas></div>
        </div>
        <div class="card">
            <div class="card-header">
                <h3>Deep Research R/R (deep_research_rr_ratio)</h3>
                <div id="corrDrStats" class="sub"></div>
            </div>
            <div class="chart-box tall"><canvas id="corrDrChart"></canvas></div>
        </div>
    </div>

    <h2>Recovery patterns</h2>
    <div class="grid grid-2">
        <div class="card">
            <div class="card-header">
                <h3>By AI council verdict</h3>
                <div class="sub">
                    Trading days from decision until pre-drop level reached, plus
                    average return at +5/+10/+20 days <em>after</em> recovery.
                </div>
            </div>
            <div class="scroll-table">
                <table id="recIntentTable">
                    <thead><tr>
                        <th>Group</th>
                        <th class="num">N total</th>
                        <th class="num">Recovered</th>
                        <th class="num">% recov</th>
                        <th class="num">p25 d</th>
                        <th class="num">p50 d</th>
                        <th class="num">p75 d</th>
                        <th class="num">+5d</th>
                        <th class="num">+10d</th>
                        <th class="num">+20d</th>
                    </tr></thead>
                    <tbody></tbody>
                </table>
            </div>
        </div>
        <div class="card">
            <div class="card-header">
                <h3>By Deep Research verdict</h3>
                <div class="sub">Same view for DR groups.</div>
            </div>
            <div class="scroll-table">
                <table id="recDrTable">
                    <thead><tr>
                        <th>Group</th>
                        <th class="num">N total</th>
                        <th class="num">Recovered</th>
                        <th class="num">% recov</th>
                        <th class="num">p25 d</th>
                        <th class="num">p50 d</th>
                        <th class="num">p75 d</th>
                        <th class="num">+5d</th>
                        <th class="num">+10d</th>
                        <th class="num">+20d</th>
                    </tr></thead>
                    <tbody></tbody>
                </table>
            </div>
        </div>
    </div>

    <h2>Per-decision explorer</h2>
    <div class="card">
        <div class="card-header filters">
            <div>
                <input type="text" id="filterText" placeholder="Filter symbol / verdict ..." />
                <select id="filterIntent">
                    <option value="">All intents</option>
                    <option value="ENTER_NOW">ENTER_NOW</option>
                    <option value="ENTER_LIMIT">ENTER_LIMIT</option>
                    <option value="AVOID">AVOID</option>
                    <option value="NEUTRAL">NEUTRAL</option>
                </select>
                <select id="filterHasReturn">
                    <option value="">All decisions</option>
                    <option value="true">Has 4w return</option>
                </select>
            </div>
            <div class="muted" id="rowCount"></div>
        </div>
        <div class="scroll-table">
            <table id="decisionTable">
                <thead><tr></tr></thead>
                <tbody></tbody>
            </table>
        </div>
    </div>
</div>

<script>
const DATA = __PAYLOAD_JSON__;

const fmtPct = v => v == null ? '—' : (v * 100).toFixed(1) + '%';
const fmtSigned = v => v == null ? '—' : (v >= 0 ? '+' : '') + (v * 100).toFixed(2) + '%';

document.getElementById('meta').textContent =
    `Cohort: ${DATA.cohort_size} decisions since ${DATA.cohort_start} · Generated ${new Date(DATA.generated_at).toLocaleString()}`;

function emptyState(canvasId, message) {
    const cv = document.getElementById(canvasId);
    const ctx = cv.getContext('2d');
    ctx.clearRect(0, 0, cv.width, cv.height);
    ctx.fillStyle = '#94a3b8';
    ctx.font = '13px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(message, cv.width / 2, cv.height / 2);
}

function makeWinrateBar(canvasId, rows, labelKey) {
    if (!rows || rows.length === 0) {
        emptyState(canvasId, 'no data');
        return;
    }
    new Chart(document.getElementById(canvasId).getContext('2d'), {
        type: 'bar',
        data: {
            labels: rows.map(r => String(r[labelKey] ?? '(none)')),
            datasets: [{
                data: rows.map(r => r.win_rate),
                backgroundColor: '#3b82f6',
            }]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: {
                legend: {display: false},
                tooltip: {callbacks: {label: c => {
                    const r = rows[c.dataIndex];
                    return `${(r.win_rate * 100).toFixed(1)}% — n=${r.count}`;
                }}}
            },
            scales: {
                y: {beginAtZero: true, max: 1.05,
                    ticks: {color: '#94a3b8', callback: v => (v * 100).toFixed(0) + '%'}},
                x: {ticks: {color: '#94a3b8'}}
            }
        }
    });
}

function makeAvgReturnBar(canvasId, rows, labelKey) {
    if (!rows || rows.length === 0) {
        emptyState(canvasId, 'no data');
        return;
    }
    new Chart(document.getElementById(canvasId).getContext('2d'), {
        type: 'bar',
        data: {
            labels: rows.map(r => String(r[labelKey] ?? '(none)')),
            datasets: [{
                data: rows.map(r => r.avg_return * 100),
                backgroundColor: rows.map(r => r.avg_return >= 0 ? '#22c55e' : '#ef4444'),
            }]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: {
                legend: {display: false},
                tooltip: {callbacks: {label: c => {
                    const r = rows[c.dataIndex];
                    return `${(r.avg_return * 100).toFixed(2)}% — n=${r.count}, win ${(r.win_rate * 100).toFixed(0)}%`;
                }}}
            },
            scales: {
                y: {ticks: {color: '#94a3b8', callback: v => v.toFixed(0) + '%'}},
                x: {ticks: {color: '#94a3b8'}}
            }
        }
    });
}

// 1. PM (AI council) verdict
makeWinrateBar('pmWinChart', DATA.winrate_by_intent, 'intent');
makeAvgReturnBar('pmRetChart', DATA.winrate_by_intent, 'intent');

// 2. DR verdict
const drVerdict = (DATA.winrate_by_dr_verdict && DATA.winrate_by_dr_verdict.length)
    ? DATA.winrate_by_dr_verdict
    : DATA.winrate_by_dr_action;
makeWinrateBar('drWinChart', drVerdict, 'deep_research_verdict' in (drVerdict[0] || {}) ? 'deep_research_verdict' : 'deep_research_action');
makeAvgReturnBar('drRetChart', drVerdict, 'deep_research_verdict' in (drVerdict[0] || {}) ? 'deep_research_verdict' : 'deep_research_action');

// 3. PM R/R bucket
makeWinrateBar('pmRRWinChart', DATA.winrate_by_pm_rr, 'bucket');
makeAvgReturnBar('pmRRRetChart', DATA.winrate_by_pm_rr, 'bucket');

// 4. DR R/R bucket
makeWinrateBar('drRRWinChart', DATA.winrate_by_dr_rr, 'bucket');
makeAvgReturnBar('drRRRetChart', DATA.winrate_by_dr_rr, 'bucket');

// ----- Time series since signal -----
const COLOR_BY_INTENT = {
    'ENTER_NOW': '#22c55e',
    'ENTER_LIMIT': '#3b82f6',
    'AVOID': '#ef4444',
    'NEUTRAL': '#94a3b8',
};
const COLOR_BY_DR = {
    'BUY': '#22c55e',
    'BUY_LIMIT': '#3b82f6',
    'AVOID': '#ef4444',
    'WATCH': '#fbbf24',
    'HOLD': '#94a3b8',
};

function colorForGroup(name, palette) {
    return palette[name] || '#cbd5e1';
}

// Subtract SPY from a group's median path to get alpha (excess vs SPY).
function asAlpha(groupMedian, spyMedian) {
    const out = [];
    for (let i = 0; i < groupMedian.length; i++) {
        const g = groupMedian[i], s = (spyMedian && i < spyMedian.length) ? spyMedian[i] : null;
        if (g == null || s == null) out.push(null);
        else out.push(g - s);
    }
    return out;
}

function renderMedianPaths(canvasId, groupsObj, palette, mode = 'absolute') {
    const groupNames = Object.keys(groupsObj || {});
    if (groupNames.length === 0) {
        emptyState(canvasId, 'no time-series data');
        return null;
    }
    const spy = (DATA.time_series || {}).spy_overlay || {};
    let maxDays = 0;
    const datasets = [];
    for (const grp of groupNames) {
        const g = groupsObj[grp];
        if (!g || !g.median) continue;
        maxDays = Math.max(maxDays, g.day_offsets.length);
        const series = mode === 'alpha' && spy.median
            ? asAlpha(g.median, spy.median)
            : g.median;
        datasets.push({
            label: `${grp} (n=${g.n_paths})`,
            data: series.map(v => v == null ? null : v * 100),
            borderColor: colorForGroup(grp, palette),
            backgroundColor: colorForGroup(grp, palette),
            tension: 0.15,
            pointRadius: 0,
            borderWidth: 2.4,
            spanGaps: true,
        });
    }
    if (mode === 'absolute' && spy.median) {
        maxDays = Math.max(maxDays, spy.median.length);
        datasets.push({
            label: 'S&P 500 (SPY median)',
            data: spy.median.map(v => v == null ? null : v * 100),
            borderColor: '#cbd5e1',
            borderWidth: 2,
            borderDash: [5, 4],
            pointRadius: 0,
            tension: 0.15,
            spanGaps: true,
        });
    }
    return new Chart(document.getElementById(canvasId).getContext('2d'), {
        type: 'line',
        data: {
            labels: Array.from({length: maxDays}, (_, i) => i),
            datasets,
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: {mode: 'index', intersect: false},
            plugins: {
                legend: {position: 'bottom', labels: {color: '#94a3b8', boxWidth: 12}},
                tooltip: {callbacks: {
                    label: c => `${c.dataset.label}: ${c.parsed.y == null ? '—' : (c.parsed.y >= 0 ? '+' : '') + c.parsed.y.toFixed(2) + '%'}`
                }}
            },
            scales: {
                y: {
                    ticks: {color: '#94a3b8', callback: v => v.toFixed(0) + '%'},
                    title: {
                        display: true,
                        text: mode === 'alpha' ? 'Excess return vs SPY' : 'Return since decision',
                        color: '#94a3b8',
                    },
                    grid: {color: 'rgba(148, 163, 184, 0.08)'},
                },
                x: {
                    ticks: {color: '#94a3b8'},
                    title: {display: true, text: 'Trading days since decision', color: '#94a3b8'},
                    grid: {color: 'rgba(148, 163, 184, 0.05)'},
                }
            }
        }
    });
}

function renderSpaghetti(canvasId) {
    const ts = DATA.time_series || {};
    const byIntent = ts.by_intent || {};
    const buys = [];
    for (const intent of ['ENTER_NOW', 'ENTER_LIMIT']) {
        const indiv = (byIntent[intent] && byIntent[intent].individuals) || [];
        for (const p of indiv) {
            buys.push({...p, _intent: intent});
        }
    }
    if (buys.length === 0) {
        emptyState(canvasId, 'no buy-signal price paths');
        return;
    }

    const datasets = buys.map(p => ({
        label: `${p.symbol} (${p.decision_date})`,
        data: p.returns.map(v => v * 100),
        borderColor: 'rgba(148, 163, 184, 0.18)',
        borderWidth: 0.8,
        pointRadius: 0,
        tension: 0,
        spanGaps: true,
        _isMedian: false,
    }));

    for (const intent of ['ENTER_NOW', 'ENTER_LIMIT']) {
        const g = byIntent[intent];
        if (!g || !g.median) continue;
        datasets.push({
            label: `${intent} median (n=${g.n_paths})`,
            data: g.median.map(v => v == null ? null : v * 100),
            borderColor: COLOR_BY_INTENT[intent],
            borderWidth: 3,
            pointRadius: 0,
            tension: 0.15,
            spanGaps: true,
            _isMedian: true,
        });
    }

    const maxDays = Math.max(...buys.map(p => p.returns.length));
    new Chart(document.getElementById(canvasId).getContext('2d'), {
        type: 'line',
        data: {labels: Array.from({length: maxDays}, (_, i) => i), datasets},
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: {
                        color: '#94a3b8',
                        boxWidth: 12,
                        filter: (item) => item.text.includes('median'),
                    },
                    position: 'bottom',
                },
                tooltip: {
                    filter: c => c.dataset._isMedian,
                    callbacks: {
                        label: c => `${c.dataset.label}: ${c.parsed.y == null ? '—' : (c.parsed.y >= 0 ? '+' : '') + c.parsed.y.toFixed(2) + '%'}`
                    }
                }
            },
            scales: {
                y: {
                    ticks: {color: '#94a3b8', callback: v => v.toFixed(0) + '%'},
                    grid: {color: 'rgba(148, 163, 184, 0.08)'},
                    title: {display: true, text: 'Return since decision', color: '#94a3b8'},
                },
                x: {
                    ticks: {color: '#94a3b8'},
                    grid: {color: 'rgba(148, 163, 184, 0.05)'},
                    title: {display: true, text: 'Trading days since decision', color: '#94a3b8'},
                }
            }
        }
    });
}

let tsIntentChart = null;
let tsDrChart = null;
function renderTimeSeriesPair(mode) {
    if (tsIntentChart) tsIntentChart.destroy();
    if (tsDrChart) tsDrChart.destroy();
    tsIntentChart = renderMedianPaths('tsIntentChart',
        (DATA.time_series || {}).by_intent, COLOR_BY_INTENT, mode);
    tsDrChart = renderMedianPaths('tsDrChart',
        (DATA.time_series || {}).by_dr_verdict, COLOR_BY_DR, mode);
}
renderTimeSeriesPair('absolute');
renderSpaghetti('tsSpaghetti');

document.querySelectorAll('#tsModeToggle button').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('#tsModeToggle button')
            .forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        renderTimeSeriesPair(btn.dataset.mode);
    });
});

// ----- Statistical significance tables -----
function pFmt(p) {
    if (p == null || isNaN(p)) return '—';
    if (p < 0.001) return '<0.001';
    return p.toFixed(3);
}
function dFmt(d) {
    if (d == null || isNaN(d)) return '—';
    return (d >= 0 ? '+' : '') + d.toFixed(2);
}
function renderSigTable(tableId, rows) {
    const tbody = document.querySelector(`#${tableId} tbody`);
    if (!rows || rows.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="empty">no significance data</td></tr>';
        return;
    }
    rows.sort((a, b) => (a.welch_p_fdr ?? 1) - (b.welch_p_fdr ?? 1));
    tbody.innerHTML = rows.map(r => {
        const sig = r.significant;
        const diffCls = (r.diff ?? 0) >= 0 ? 'pos' : 'neg';
        const diffStr = (r.diff == null) ? '—' :
            ((r.diff >= 0 ? '+' : '') + (r.diff * 100).toFixed(2) + '%');
        return `
            <tr>
                <td>${r.group_a}</td>
                <td>${r.group_b}</td>
                <td class="num">${r.n_a} / ${r.n_b}</td>
                <td class="num ${diffCls}">${diffStr}</td>
                <td class="num">${dFmt(r.cohen_d)}</td>
                <td class="num">${pFmt(r.welch_p_fdr)}</td>
                <td class="num">${pFmt(r.mwu_p_fdr)}</td>
                <td><span class="badge ${sig ? 'sig' : 'ns'}">${sig ? 'p<0.05' : 'n.s.'}</span></td>
            </tr>`;
    }).join('');
}
renderSigTable('sigIntentTable', (DATA.stats || {}).pairwise_intent || []);
renderSigTable('sigDrTable', (DATA.stats || {}).pairwise_dr_verdict || []);

// ----- Correlation scatter plots -----
function renderCorrelation(canvasId, statsId, corrData, label) {
    const el = document.getElementById(statsId);
    if (!corrData || corrData.n < 5) {
        el.textContent = `n=${corrData ? corrData.n : 0} — too few points for correlation.`;
        emptyState(canvasId, 'too few points');
        return;
    }
    const r = corrData.pearson_r, p = corrData.pearson_p;
    const rho = corrData.spearman_rho, prho = corrData.spearman_p;
    el.innerHTML = `
        <strong>n=${corrData.n}</strong>
        Pearson r=${r != null ? r.toFixed(3) : '—'} (p=${pFmt(p)})
        · Spearman ρ=${rho != null ? rho.toFixed(3) : '—'} (p=${pFmt(prho)})
        · slope=${corrData.regression_slope != null ? corrData.regression_slope.toFixed(3) : '—'}
    `;

    const points = corrData.points || [];
    const datasets = [{
        type: 'scatter',
        label: label,
        data: points.map(p => ({x: p.x, y: p.y * 100})),
        backgroundColor: 'rgba(96, 165, 250, 0.55)',
        borderColor: 'rgba(96, 165, 250, 0.9)',
        pointRadius: 3,
    }];
    if (corrData.regression_slope != null && points.length > 1) {
        const xs = points.map(p => p.x);
        const xmin = Math.min(...xs), xmax = Math.max(...xs);
        const slope = corrData.regression_slope, b = corrData.regression_intercept;
        datasets.push({
            type: 'line',
            label: 'OLS fit',
            data: [{x: xmin, y: (slope * xmin + b) * 100},
                   {x: xmax, y: (slope * xmax + b) * 100}],
            borderColor: '#fbbf24',
            backgroundColor: 'transparent',
            borderWidth: 2,
            pointRadius: 0,
            tension: 0,
        });
    }

    new Chart(document.getElementById(canvasId).getContext('2d'), {
        data: {datasets},
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: {
                legend: {labels: {color: '#94a3b8', boxWidth: 12}, position: 'bottom'},
                tooltip: {callbacks: {label: c => {
                    const x = c.parsed.x, y = c.parsed.y;
                    return `R/R=${x.toFixed(2)}, return=${y.toFixed(2)}%`;
                }}}
            },
            scales: {
                x: {
                    type: 'linear',
                    title: {display: true, text: 'R/R ratio', color: '#94a3b8'},
                    ticks: {color: '#94a3b8'},
                    grid: {color: 'rgba(148, 163, 184, 0.05)'},
                },
                y: {
                    title: {display: true, text: '4-week return', color: '#94a3b8'},
                    ticks: {color: '#94a3b8', callback: v => v.toFixed(0) + '%'},
                    grid: {color: 'rgba(148, 163, 184, 0.08)'},
                }
            }
        }
    });
}
renderCorrelation('corrPmChart', 'corrPmStats', (DATA.stats || {}).corr_pm_rr, 'PM R/R');
renderCorrelation('corrDrChart', 'corrDrStats', (DATA.stats || {}).corr_dr_rr, 'DR R/R');

// ----- Recovery tables -----
function renderRecoveryTable(tableId, rows) {
    const tbody = document.querySelector(`#${tableId} tbody`);
    if (!rows || rows.length === 0) {
        tbody.innerHTML = '<tr><td colspan="10" class="empty">no recovery data</td></tr>';
        return;
    }
    const dFmtDay = v => v == null ? '—' : v.toFixed(1);
    const sFmt = v => v == null ? '—' : (v >= 0 ? '+' : '') + (v * 100).toFixed(2) + '%';
    const sCls = v => v == null ? '' : (v >= 0 ? 'pos' : 'neg');
    tbody.innerHTML = rows.map(r => `
        <tr>
            <td>${r.group}</td>
            <td class="num">${r.n_total}</td>
            <td class="num">${r.n_recovered}</td>
            <td class="num">${r.recovery_rate == null ? '—' : (r.recovery_rate * 100).toFixed(0) + '%'}</td>
            <td class="num">${dFmtDay(r.p25_days)}</td>
            <td class="num">${dFmtDay(r.p50_days)}</td>
            <td class="num">${dFmtDay(r.p75_days)}</td>
            <td class="num ${sCls(r.post_recover_5d_mean)}">${sFmt(r.post_recover_5d_mean)}</td>
            <td class="num ${sCls(r.post_recover_10d_mean)}">${sFmt(r.post_recover_10d_mean)}</td>
            <td class="num ${sCls(r.post_recover_20d_mean)}">${sFmt(r.post_recover_20d_mean)}</td>
        </tr>
    `).join('');
}
renderRecoveryTable('recIntentTable', (DATA.stats || {}).recovery_by_intent || []);
renderRecoveryTable('recDrTable', (DATA.stats || {}).recovery_by_dr_verdict || []);

// ----- Per-decision explorer -----
const COLS = [
    {key: 'decision_date', label: 'Date', type: 'str'},
    {key: 'symbol', label: 'Symbol', type: 'str'},
    {key: 'intent', label: 'PM intent', type: 'pill'},
    {key: 'recommendation', label: 'PM rec', type: 'str'},
    {key: 'deep_research_verdict', label: 'DR verdict', type: 'str'},
    {key: 'risk_reward_ratio', label: 'PM R/R', type: 'num2'},
    {key: 'deep_research_rr_ratio', label: 'DR R/R', type: 'num2'},
    {key: 'drop_percent', label: 'Drop %', type: 'pct_signed'},
    {key: 'price_at_decision', label: 'Price', type: 'usd'},
    {key: 'return_4w', label: '4w return', type: 'pct_signed'},
    {key: 'return_8w', label: '8w return', type: 'pct_signed'},
    {key: 'max_roi_4w', label: 'Max 4w', type: 'pct_signed'},
    {key: 'max_drawdown_4w', label: 'DD 4w', type: 'pct_signed'},
];

let sortState = {key: 'decision_date', dir: 'desc'};

const thRow = document.querySelector('#decisionTable thead tr');
thRow.innerHTML = COLS.map(col => {
    const numClass = ['pct_signed', 'usd', 'num2'].includes(col.type) ? ' class="num"' : '';
    return `<th data-key="${col.key}"${numClass}>${col.label}</th>`;
}).join('');
thRow.querySelectorAll('th').forEach(th => {
    th.addEventListener('click', () => {
        const k = th.dataset.key;
        if (sortState.key === k) sortState.dir = sortState.dir === 'asc' ? 'desc' : 'asc';
        else { sortState.key = k; sortState.dir = 'desc'; }
        renderTable();
    });
});

function renderCell(value, type) {
    if (value == null) return '<td class="muted">—</td>';
    switch (type) {
        case 'pct_signed': {
            const cls = value >= 0 ? 'pos' : 'neg';
            return `<td class="num ${cls}">${(value >= 0 ? '+' : '') + (value * 100).toFixed(2)}%</td>`;
        }
        case 'usd': return `<td class="num">$${Number(value).toFixed(2)}</td>`;
        case 'num2': return `<td class="num">${Number(value).toFixed(2)}</td>`;
        case 'pill': {
            const v = String(value);
            return `<td><span class="pill ${v.toLowerCase()}">${v}</span></td>`;
        }
        case 'str':
        default: return `<td>${value}</td>`;
    }
}

function renderTable() {
    const text = document.getElementById('filterText').value.toLowerCase();
    const intent = document.getElementById('filterIntent').value;
    const hasReturn = document.getElementById('filterHasReturn').value;

    let rows = (DATA.decisions || []).filter(d => {
        if (intent && d.intent !== intent) return false;
        if (hasReturn === 'true' && d.return_4w == null) return false;
        if (text) {
            const blob = [d.symbol, d.recommendation, d.deep_research_verdict,
                          d.deep_research_action, d.sector]
                .filter(Boolean).join(' ').toLowerCase();
            if (!blob.includes(text)) return false;
        }
        return true;
    });

    const k = sortState.key, dir = sortState.dir === 'asc' ? 1 : -1;
    rows.sort((a, b) => {
        const va = a[k], vb = b[k];
        if (va == null && vb == null) return 0;
        if (va == null) return 1;
        if (vb == null) return -1;
        if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir;
        return String(va).localeCompare(String(vb)) * dir;
    });

    document.getElementById('rowCount').textContent =
        `${rows.length} of ${(DATA.decisions || []).length} decisions`;

    document.querySelectorAll('#decisionTable th').forEach(th => {
        th.classList.remove('sort-asc', 'sort-desc');
        if (th.dataset.key === sortState.key) {
            th.classList.add(sortState.dir === 'asc' ? 'sort-asc' : 'sort-desc');
        }
    });

    const tbody = document.querySelector('#decisionTable tbody');
    tbody.innerHTML = rows.map(d => {
        return '<tr>' + COLS.map(col => {
            if (col.key === 'symbol') {
                return `<td><a class="ticker" href="https://finance.yahoo.com/quote/${d.symbol}" target="_blank" rel="noopener">${d.symbol}</a></td>`;
            }
            return renderCell(d[col.key], col.type);
        }).join('') + '</tr>';
    }).join('');
}

document.getElementById('filterText').addEventListener('input', renderTable);
document.getElementById('filterIntent').addEventListener('change', renderTable);
document.getElementById('filterHasReturn').addEventListener('change', renderTable);

renderTable();
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-02-01", help="Cohort start date (or 'all')")
    today = datetime.now().strftime("%Y-%m-%d")
    parser.add_argument("--out", default=f"docs/performance/{today}-deep-dive.html")
    args = parser.parse_args()

    start = None if args.start == "all" else args.start
    logger.info("Building payload (start=%s)...", start)
    payload = build_payload(start_date=start or "all")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payload_json = json.dumps(payload, default=str, separators=(",", ":"))
    payload_json = payload_json.replace("</", "<\\/")  # safe to inline in <script>

    html = HTML_TEMPLATE.replace("__PAYLOAD_JSON__", payload_json)
    out_path.write_text(html)
    logger.info("Wrote %s (%.1f KB, %d decisions)",
                out_path, len(html) / 1024, payload["cohort_size"])


if __name__ == "__main__":
    main()
