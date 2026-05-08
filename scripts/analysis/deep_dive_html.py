"""Generate a self-contained interactive HTML deep-dive report.

Usage:
    ./venv/bin/python scripts/analysis/deep_dive_html.py [--start 2026-02-01] [--out PATH]

Produces a single HTML file with Chart.js charts (loaded from CDN) and a
sortable/filterable per-decision explorer table. Open it directly in a browser —
no server required.
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
<title>StockDrop Performance Deep-Dive</title>
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
    h2 { font-size: 1.15rem; margin: 28px 0 12px 0; padding-bottom: 6px;
         border-bottom: 1px solid var(--border); }
    h3 { font-size: 0.95rem; margin: 0; font-weight: 600; }
    .muted { color: var(--muted); font-size: 0.85rem; }
    .container { max-width: 1280px; margin: 0 auto; }
    .grid { display: grid; gap: 14px; }
    .grid-4 { grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
    .grid-2 { grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); }
    .card { background: var(--panel); border: 1px solid var(--border);
            border-radius: 8px; padding: 16px; }
    .card-header { padding-bottom: 10px; border-bottom: 1px solid var(--border);
                   margin-bottom: 12px; display: flex; justify-content: space-between;
                   align-items: center; gap: 8px; flex-wrap: wrap; }
    .metric-label { font-size: 0.78rem; color: var(--muted); text-transform: uppercase;
                    letter-spacing: 0.05em; }
    .metric-value { font-size: 1.6rem; font-weight: 700; margin: 4px 0; }
    .metric-sub { font-size: 0.78rem; color: var(--muted); }
    .chart-box { height: 260px; position: relative; }
    .chart-box.tall { height: 320px; }
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
    .filters { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
    .scroll-table { max-height: 540px; overflow-y: auto; }
    .horizon-tabs { display: inline-flex; gap: 4px; }
    .horizon-tabs button { background: var(--bg); color: var(--text);
                           border: 1px solid var(--border); padding: 4px 12px;
                           border-radius: 4px; cursor: pointer; font-size: 0.82rem; }
    .horizon-tabs button.active { background: var(--accent); color: #0b1220;
                                  border-color: var(--accent); }
</style>
</head>
<body>
<div class="container">
    <h1>StockDrop Performance Deep-Dive</h1>
    <div class="muted" id="meta"></div>

    <h2>Headline</h2>
    <div class="grid grid-4" id="headlineGrid"></div>

    <h2>Equity curve</h2>
    <div class="card">
        <div class="card-header">
            <h3>Equal-weight cumulative growth — BUY/BUY_LIMIT, 4w returns</h3>
        </div>
        <div class="chart-box tall"><canvas id="equityChart"></canvas></div>
    </div>

    <h2>Win rate by group (4w)</h2>
    <div class="grid grid-2">
        <div class="card">
            <div class="card-header">
                <h3>By intent</h3>
                <div class="horizon-tabs" id="intentHorizonTabs">
                    <button data-h="1w">1w</button>
                    <button data-h="2w">2w</button>
                    <button data-h="4w" class="active">4w</button>
                    <button data-h="8w">8w</button>
                </div>
            </div>
            <div class="chart-box"><canvas id="intentChart"></canvas></div>
        </div>
        <div class="card">
            <div class="card-header"><h3>By drop-size bucket</h3></div>
            <div class="chart-box"><canvas id="dropChart"></canvas></div>
        </div>
        <div class="card">
            <div class="card-header"><h3>By Deep Research action</h3></div>
            <div class="chart-box"><canvas id="drChart"></canvas></div>
        </div>
        <div class="card">
            <div class="card-header"><h3>By gatekeeper tier</h3></div>
            <div class="chart-box"><canvas id="gateChart"></canvas></div>
        </div>
    </div>

    <h2>Time to recovery</h2>
    <div class="card">
        <div class="card-header">
            <h3>Trading days until pre-drop price reached (capped at 40)</h3>
        </div>
        <div class="chart-box"><canvas id="recoverChart"></canvas></div>
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
                <select id="filterRecovered">
                    <option value="">Recovered: any</option>
                    <option value="true">Recovered</option>
                    <option value="false">Not recovered</option>
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

    <h2>Per-horizon table</h2>
    <div class="card scroll-table">
        <table id="horizonTable">
            <thead><tr>
                <th>Horizon</th><th>Intent</th>
                <th class="num">N</th><th class="num">Win rate</th><th class="num">Avg return</th>
            </tr></thead>
            <tbody></tbody>
        </table>
    </div>
</div>

<script>
const DATA = __PAYLOAD_JSON__;

const fmtPct = v => v == null ? '—' : (v * 100).toFixed(1) + '%';
const fmtSigned = v => v == null ? '—' : (v >= 0 ? '+' : '') + (v * 100).toFixed(2) + '%';
const colorRet = v => v == null ? '#6b7280' : (v >= 0 ? '#22c55e' : '#ef4444');

document.getElementById('meta').textContent =
    `Cohort: ${DATA.cohort_size} decisions since ${DATA.cohort_start} · Generated ${new Date(DATA.generated_at).toLocaleString()}`;

// Headline cards
const h = DATA.headline || {};
const cards = [
    {label: 'Win rate (4w, BUY/BUY_LIMIT)',
     value: fmtPct(h.win_rate_4w_buys),
     sub: `avg ${fmtSigned(h.avg_return_4w_buys)} (n=${h.n_buys_4w ?? 0})`},
    {label: 'BUY_LIMIT fill rate',
     value: fmtPct(h.buy_limit_fill_rate),
     sub: `${h.buy_limit_filled ?? 0}/${h.buy_limit_count ?? 0} filled · avg ${fmtSigned(h.buy_limit_avg_filled_4w)}`},
    {label: 'Median days to recover',
     value: h.median_days_to_recover != null ? h.median_days_to_recover.toFixed(1) : '—',
     sub: `${h.n_recovered ?? 0} recovered (8w window)`},
    {label: 'Cohort size',
     value: DATA.cohort_size,
     sub: `since ${DATA.cohort_start}`},
];
document.getElementById('headlineGrid').innerHTML = cards.map(c => `
    <div class="card">
        <div class="metric-label">${c.label}</div>
        <div class="metric-value">${c.value}</div>
        <div class="metric-sub">${c.sub}</div>
    </div>
`).join('');

// Equity curve
const eq = DATA.equity_curve || [];
new Chart(document.getElementById('equityChart').getContext('2d'), {
    type: 'line',
    data: {
        labels: eq.map(p => p.decision_date),
        datasets: [{label: 'Equity', data: eq.map(p => p.equity),
                    borderColor: '#60a5fa', tension: 0.1, pointRadius: 2, fill: false}]
    },
    options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {legend: {display: false}},
        scales: {y: {ticks: {color: '#94a3b8'}}, x: {ticks: {color: '#94a3b8'}}}
    }
});

function makeWinrateBar(canvasId, rows, labelKey) {
    if (!rows || rows.length === 0) {
        const c = document.getElementById(canvasId).getContext('2d');
        c.fillStyle = '#94a3b8';
        c.font = '14px sans-serif';
        c.fillText('no data', 20, 40);
        return null;
    }
    return new Chart(document.getElementById(canvasId).getContext('2d'), {
        type: 'bar',
        data: {
            labels: rows.map(r => String(r[labelKey] ?? '(none)')),
            datasets: [{
                label: 'Win rate',
                data: rows.map(r => r.win_rate),
                backgroundColor: '#3b82f6',
            }]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: {
                legend: {display: false},
                tooltip: {callbacks: {label: (c) => {
                    const r = rows[c.dataIndex];
                    return `${(r.win_rate * 100).toFixed(1)}% — n=${r.count}, avg ${(r.avg_return * 100).toFixed(2)}%`;
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

let intentChart = null;
function renderIntent(horizon) {
    const rows = (DATA.winrate_by_horizon || []).filter(r => r.horizon === horizon)
        .map(r => ({intent: r.intent, win_rate: r.win_rate, avg_return: r.avg_return, count: r.n}));
    if (intentChart) intentChart.destroy();
    intentChart = makeWinrateBar('intentChart', rows, 'intent');
}
renderIntent('4w');
document.querySelectorAll('#intentHorizonTabs button').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('#intentHorizonTabs button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        renderIntent(btn.dataset.h);
    });
});

makeWinrateBar('dropChart', DATA.winrate_by_drop_bucket, 'bucket');
makeWinrateBar('drChart', DATA.winrate_by_dr_action, 'deep_research_action');
makeWinrateBar('gateChart', DATA.winrate_by_gatekeeper, 'gatekeeper_tier');

const rec = DATA.time_to_recover || [];
new Chart(document.getElementById('recoverChart').getContext('2d'), {
    type: 'bar',
    data: {labels: rec.map(p => p.days),
           datasets: [{label: 'Count', data: rec.map(p => p.count), backgroundColor: '#8b5cf6'}]},
    options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {legend: {display: false}},
        scales: {y: {ticks: {color: '#94a3b8'}}, x: {ticks: {color: '#94a3b8'}}}
    }
});

// Horizon table
const horizonOrder = {'1w': 1, '2w': 2, '4w': 3, '8w': 4};
const hRows = [...(DATA.winrate_by_horizon || [])]
    .sort((a, b) => (horizonOrder[a.horizon] - horizonOrder[b.horizon]) || a.intent.localeCompare(b.intent));
document.querySelector('#horizonTable tbody').innerHTML = hRows.map(r => `
    <tr>
        <td>${r.horizon}</td>
        <td><span class="pill ${r.intent.toLowerCase()}">${r.intent}</span></td>
        <td class="num">${r.n}</td>
        <td class="num">${(r.win_rate * 100).toFixed(0)}%</td>
        <td class="num ${r.avg_return >= 0 ? 'pos' : 'neg'}">${(r.avg_return * 100).toFixed(2)}%</td>
    </tr>
`).join('');

// ----- Per-decision explorer -----
const COLS = [
    {key: 'decision_date', label: 'Date', type: 'str'},
    {key: 'symbol', label: 'Symbol', type: 'str'},
    {key: 'intent', label: 'Intent', type: 'pill'},
    {key: 'recommendation', label: 'PM rec', type: 'str'},
    {key: 'drop_percent', label: 'Drop %', type: 'pct_signed'},
    {key: 'price_at_decision', label: 'Price', type: 'usd'},
    {key: 'sector', label: 'Sector', type: 'str'},
    {key: 'gatekeeper_tier', label: 'Gate', type: 'str'},
    {key: 'deep_research_action', label: 'DR', type: 'str'},
    {key: 'return_1w', label: '1w', type: 'pct_signed'},
    {key: 'return_2w', label: '2w', type: 'pct_signed'},
    {key: 'return_4w', label: '4w', type: 'pct_signed'},
    {key: 'return_8w', label: '8w', type: 'pct_signed'},
    {key: 'max_roi_4w', label: 'Max 4w', type: 'pct_signed'},
    {key: 'max_drawdown_4w', label: 'DD 4w', type: 'pct_signed'},
    {key: 'limit_filled', label: 'Filled?', type: 'bool'},
    {key: 'recovered', label: 'Recov?', type: 'bool'},
    {key: 'days_to_recover', label: 'd-to-rec', type: 'int'},
];

let sortState = {key: 'decision_date', dir: 'desc'};

const thRow = document.querySelector('#decisionTable thead tr');
thRow.innerHTML = COLS.map(col => {
    const numClass = ['pct_signed', 'usd', 'int'].includes(col.type) ? ' class="num"' : '';
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
        case 'int': return `<td class="num">${Number(value)}</td>`;
        case 'bool': return `<td>${value ? '✓' : '·'}</td>`;
        case 'pill': {
            const v = String(value);
            return `<td><span class="pill ${v.toLowerCase()}">${v}</span></td>`;
        }
        case 'str':
        default:
            if (value === 'symbol') return `<td>${value}</td>`;
            return `<td>${value}</td>`;
    }
}

function renderTable() {
    const text = document.getElementById('filterText').value.toLowerCase();
    const intent = document.getElementById('filterIntent').value;
    const recovered = document.getElementById('filterRecovered').value;

    let rows = (DATA.decisions || []).filter(d => {
        if (intent && d.intent !== intent) return false;
        if (recovered === 'true' && !d.recovered) return false;
        if (recovered === 'false' && d.recovered) return false;
        if (text) {
            const blob = [d.symbol, d.recommendation, d.deep_research_action, d.deep_research_verdict, d.sector]
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

    document.getElementById('rowCount').textContent = `${rows.length} of ${(DATA.decisions || []).length} decisions`;

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
document.getElementById('filterRecovered').addEventListener('change', renderTable);

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
    # Defensive: keep payload safe inside <script>
    payload_json = payload_json.replace("</", "<\\/")

    html = HTML_TEMPLATE.replace("__PAYLOAD_JSON__", payload_json)
    out_path.write_text(html)
    logger.info("Wrote %s (%.1f KB, %d decisions)",
                out_path, len(html) / 1024, payload["cohort_size"])


if __name__ == "__main__":
    main()
