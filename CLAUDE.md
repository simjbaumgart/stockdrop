CLAUDE.md — StockDrop (Stock-Tracker)

Automated stock dip-buying tool. Screens for large-cap stocks dropping >5% in a single day, runs multi-agent AI analysis, and produces buy/sell recommendations. Core thesis: forecast which stocks recover from steep single-day drops.

Runs as a **FastAPI web service** with background workers, HTML dashboard, and API endpoints.

## Project structure

```
Stock-Tracker/
├── main.py                    # FastAPI app — starts background tasks (scanner, uploads, email, tracking)
├── Procfile                   # Deployment: uvicorn main:app --host 0.0.0.0 --port $PORT
├── runtime.txt                # Python 3.9.6
├── requirements.txt
├── render.yaml                # Render deployment config
├── app/
│   ├── database.py            # SQLite schema + migrations (subscribers.db)
│   ├── utils.py
│   ├── models/
│   │   └── market_state.py    # State management for agent workflows
│   ├── routers/
│   │   ├── api.py             # REST API endpoints
│   │   ├── views.py           # HTML template views (dashboard)
│   │   ├── performance.py     # Performance tracking routes
│   │   └── subscriptions.py   # Email subscription routes
│   ├── services/              # All business logic
│   │   ├── research_service.py       # Agent orchestration (Phase 1 + 2 + PM)
│   │   ├── deep_research_service.py  # Senior reviewer with dual priority queues
│   │   ├── decision_gate_service.py  # Post-PM deterministic gates + degeneracy monitors
│   │   ├── calibration_service.py    # Pinned base-rate card injection (Tier 2)
│   │   ├── gatekeeper_service.py     # Bollinger %B + market regime pre-filter
│   │   ├── stock_service.py          # Stock screening + drop detection
│   │   ├── tradingview_service.py    # TradingView data (no API key)
│   │   ├── benzinga_service.py       # News + earnings data
│   │   ├── alpha_vantage_service.py  # Stock data
│   │   ├── finnhub_service.py        # Market data
│   │   ├── polygon_service.py        # Market data
│   │   ├── seeking_alpha_service.py  # Analyst sentiment (via RapidAPI)
│   │   ├── fred_service.py           # Federal Reserve macro data
│   │   ├── alpaca_service.py         # Stock trading/screening
│   │   ├── email_service.py          # Daily email summaries
│   │   ├── storage_service.py        # Google Cloud Storage uploads
│   │   ├── tracking_service.py       # Decision outcome tracking
│   │   ├── performance_service.py    # Trading performance metrics
│   │   ├── analyst_service.py        # Analyst data
│   │   ├── evidence_service.py       # Evidence collection
│   │   ├── quality_control_service.py # QC checks
│   │   ├── drive_service.py          # Google Drive integration
│   │   └── yahoo_ticker_resolver.py  # Ticker symbol resolution
│   └── utils/
│       └── pruning.py
├── templates/                 # Jinja2 HTML templates (dashboard UI)
├── static/                    # CSS and static assets
├── scripts/                   # Standalone utilities (20+ subdirectories)
│   ├── core/                  # Core operational scripts
│   ├── analysis/              # Data analysis tools
│   ├── automation/            # Automated workflows
│   ├── reassess_positions.py  # Sell Council: re-runs sensors with sell-focused prompts
│   └── ...
├── tests/                     # 40+ test files (pytest)
├── notebooks/                 # Jupyter notebooks
├── docs/
│   ├── images/
│   ├── audits/                # FINDINGS_LEDGER.md + MONTHLY_AUDIT_RUNBOOK.md (governance)
│   └── proposals/             # Design docs and implementation plans
├── archive/                   # Historical/archived code
├── logs/                      # Log files
└── experiment_data/           # Experimental data
```

## Architecture

### Pipeline flow

```
Screener (>5% drop)
  -> Gatekeeper (Bollinger %B < 0.50 + SPY vs SMA200 regime check)
    -> Council 1: Phase 1 sensor agents run in parallel (ThreadPoolExecutor, max_workers=8)
        - Technical Analysis
        - News Analysis
        - Market Sentiment Analysis
        - Competitive Landscape
        - Seeking Alpha
      -> Council 2: Phase 2 debate agents in parallel (ThreadPoolExecutor, max_workers=6)
          - Bull Researcher
          - Bear Researcher
          - Risk Management Agent
        -> Fund Manager (PM): synthesizes all reports
            Verdict: BUY / BUY_LIMIT / WATCH / AVOID
          -> Decision Gates (decision_gate_service.py): deterministic post-PM rules
              Drop-type, SA-quant, news-sentiment, unconfirmed-drop (knife gate suspended)
              Downgrade weak buys to WATCH; PM action preserved as pre_gate_action
              Each gate input has a trailing-50 degeneracy monitor (auto-suspend)
            -> Deep Research: Senior Investment Reviewer
                Can OVERRIDE the PM recommendation
                Can lift a gated WATCH back to BUY_LIMIT — only with a
                structured NAMED_EVENT catalyst (specific, dated, verifiable)
                Uses deep-research-pro model with Google Search grounding
                Has its own two-queue worker thread (individual=high priority, batch=low)
                Rate limited: 60s between requests
```

### Feedback loop (three-tier — see docs/proposals/THREE_TIER_FEEDBACK_PROPOSAL.md)

- **Tier 1 — code gates:** findings confirmed in ≥3 audits become deterministic post-PM rules in `decision_gate_service.py`. Invisible to agents.
- **Tier 2 — pinned base rates:** `data/calibration_card_pinned.json`, injected into PM/DR prompts as data only (`CALIBRATION_ENABLED`). Written ONLY by `scripts/analysis/pin_calibration_card.py` after a monthly audit; 45-day staleness refusal. Nightly job builds a console-only candidate — it never auto-propagates.
- **Tier 3 — console only:** rolling per-verdict alpha and everything regime-dependent or small-n. Never shown to agents.
- **Anti-goal:** no "you were often wrong about X" narratives in any prompt — mistake-signals mode-collapse agents (falling-knife verdict hit 97% YES and was suspended).
- Stage-1 shadow A/B runs in prod (`CALIBRATION_SHADOW=1`); `/health` surfaces outcome-pipe freshness and shadow progress.

### Background tasks (started at FastAPI startup)

- Periodic stock scanner (20-minute interval)
- Google Cloud Storage data upload
- Daily email summary generation
- Performance tracking metrics
- Trade report CSV generation (60-minute interval)
- Nightly outcome marking (`decision_outcomes` forward returns) + gate degeneracy check + calibration-card QC

### Threading model

- **Main loop:** asyncio event loop (FastAPI/uvicorn)
- **Phase 1 agents:** dispatched via `ThreadPoolExecutor(max_workers=8)`
- **Phase 2 agents:** dispatched via `ThreadPoolExecutor(max_workers=6)`
- **Deep research:** separate daemon worker thread with dual priority queues (`Queue`)
- **Rule:** never do blocking I/O on the asyncio event loop. All blocking calls must go through the executor or `asyncio.to_thread()`.

### Database

SQLite (`subscribers.db`) with these core tables:

- **`decision_points`** — stores each analysis run (ticker, date, verdicts, agent reports, scores, entry/exit prices, deep research fields). 40+ columns with migration history.
- **`decision_outcomes`** — fixed-horizon forward returns per decision (1/2/4w), backfilled from yfinance and forward-marked nightly. Source of truth for all outcome analysis.
- **`decision_tracking`** — DEPRECATED (2026-07-02): raw price log with no scheduled writer. Do not build against it; use `decision_outcomes` / `get_outcomes_joined`.
- **`batch_comparisons`** — tracks batch comparison runs across candidates
- **`subscribers`** — email subscription management

Buys default to a 4-week reassess cadence (`reassess_in_days`); the Sell Council `--due` filter is driven by it.

### External data sources

TradingView (no API key), Benzinga, Alpha Vantage, Finnhub, yfinance, Polygon, Seeking Alpha (via RapidAPI), FRED, Alpaca, Google Cloud Storage

### AI models

Google Gemini models throughout. Prefer `gemini-3.1-pro-preview` for important agent calls (PM, deep research). Flash model (`gemini-3-flash-preview`) for faster inference where latency matters. Google GenAI V2 SDK used for search grounding.

## Development guidelines

### Code style

- Python 3.9+ with type hints on all new functions
- Use `async def` for any function that performs I/O
- Never `time.sleep()` in async code — use `asyncio.sleep()` or proper rate limiting
- All agent functions must return standardized report dicts (not free-form strings)
- Keep agent prompts in dedicated files or constants, not inline

### What to be careful with

- **Deep research rate limiting:** 60-second minimum between requests. Don't bypass or reduce this — it's a hard API constraint.
- **Agent prompts:** changes to PM or deep research prompts affect recommendation quality. Understand the full pipeline before modifying.
- **Gatekeeper thresholds:** Bollinger %B < 0.50 and SPY/SMA200 regime check are deliberate filters. Don't loosen without understanding false-positive impact.
- **API keys:** loaded from environment variables. Never hardcode. Required keys: `GEMINI_API_KEY`, `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPHA_VANTAGE_API_KEY`, `BENZINGA_API_KEY`, `FINNHUB_API_KEY`, `POLYGON_API_KEY`, `RAPIDAPI_KEY_SEEKING_ALPHA`, `FRED_API_KEY`, plus Google Cloud credentials.
- **SQLite writes:** the database is accessed from multiple threads. Ensure proper connection handling (one connection per thread).
- **decision_points schema:** 40+ columns with extensive migration history. Check `app/database.py` before adding columns.
- **Decision gates:** every gate encodes a finding from `docs/audits/FINDINGS_LEDGER.md`. Read the ledger before adding, removing, or re-enabling a gate. `RISK_KNIFE_GATE_ENABLED` is a deliberate manual switch — the degeneracy monitor never flips it.
- **Calibration card:** `data/calibration_card_pinned.json` is written ONLY by `scripts/analysis/pin_calibration_card.py` (then committed — prod reads the repo). Never hand-edit it, and never paste base-rate numbers directly into prompts or gate docstrings (single-source rule).

### Testing

- Test suite lives in `tests/` with 40+ test files
- Use pytest with pytest-asyncio for async code
- Integration tests should hit real APIs where feasible (past incident: mocks masked a real failure)
- For quick manual validation: run the pipeline against a known recent drop and verify the report structure

## Running the project

```bash
# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp .env.example .env
# Required: GEMINI_API_KEY, ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPHA_VANTAGE_API_KEY,
#           BENZINGA_API_KEY, FINNHUB_API_KEY, POLYGON_API_KEY, RAPIDAPI_KEY_SEEKING_ALPHA,
#           FRED_API_KEY, plus Google Cloud credentials

# Run the web service (development)
uvicorn main:app --reload

# Run the web service (production / Render)
uvicorn main:app --host 0.0.0.0 --port $PORT

# Run deep research backfill
./run_deep_research.sh [--date 2026-02-07] [--dry-run] [--limit 3]
```

## Active design documents

These docs describe planned or in-progress features. Read them before implementing related changes:

- **Three-Tier Feedback (IMPLEMENTED, PR #18):** `docs/proposals/THREE_TIER_FEEDBACK_PROPOSAL.md` — gates / pinned base rates / console-only; governs what may flow into prompts
- **Findings Ledger:** `docs/audits/FINDINGS_LEDGER.md` — evidence tier per finding, promotion rules, review dates for every live gate
- **Monthly Audit Runbook:** `docs/audits/MONTHLY_AUDIT_RUNBOOK.md` — the loop that re-earns every gate and prompt line (next: ~Aug 1)
- **LOO (Limit Order Optimizer):** `docs/proposals/LOO_Implementation_Plan.docx` — 4-phase funnel (Scan -> Validate -> Score -> Present) for monitoring BUY_LIMIT recs approaching entry range
- **Technical Dual-Track:** `docs/proposals/TECHNICAL_DUALTRACK_PROPOSAL.md` — deterministic risk flags from raw TradingView data alongside LLM analysis, replacing fragile string-matching
- **Sell Council (Plan A):** `scripts/reassess_positions.py` — re-runs sensors + deep research with sell-focused prompts for owned positions
- **Sell Price Extension (Plan B):** `docs/proposals/PLAN_sell_price_implementation.md` — extends deep research to output sell_price_low/high/ceiling_exit/exit_trigger at initial analysis time

## Known improvement backlog

Prioritized roughly by impact:

1. **Build backtesting harness** — highest impact missing piece. Replay historical drops through the pipeline and measure recommendation accuracy.
2. **Tiered Bollinger gate** — replace flat %B < 0.50 with graduated tiers (`docs/proposals/PLAN_tiered_bollinger_gate.md`).
3. **Async rate limiting** — replace any remaining `time.sleep()` with proper async patterns.
4. **Parallel data pre-fetch** — fetch data concurrently for screened candidates instead of sequentially.
5. **Add volume profile analysis** — compare current volume to 20-day average.
6. **Options market data** — IV, put/call ratios, unusual activity as additional signal.
7. **Gradient market regime** — replace binary SPY/SMA200 check with continuous signal (`docs/proposals/PLAN_volatility_regime_signal.md`).

Done (removed from backlog): extended evaluation window (now 4w via `decision_outcomes`), feedback loop (three-tier architecture, PR #18).
