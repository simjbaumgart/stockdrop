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
            Verdict: BUY / BUY_LIMIT / WAIT_FOR_STAB / PASS
          -> Deep Research: Senior Investment Reviewer
              Can OVERRIDE the PM recommendation
              Uses deep-research-pro model with Google Search grounding
              Has its own two-queue worker thread (individual=high priority, batch=low)
              Rate limited: 60s between requests
```

### Background tasks (started at FastAPI startup)

- Periodic stock scanner (20-minute interval)
- Google Cloud Storage data upload
- Daily email summary generation
- Performance tracking metrics
- Trade report CSV generation (60-minute interval)

### Threading model

- **Main loop:** asyncio event loop (FastAPI/uvicorn)
- **Phase 1 agents:** dispatched via `ThreadPoolExecutor(max_workers=8)`
- **Phase 2 agents:** dispatched via `ThreadPoolExecutor(max_workers=6)`
- **Deep research:** separate daemon worker thread with dual priority queues (`Queue`)
- **Rule:** never do blocking I/O on the asyncio event loop. All blocking calls must go through the executor or `asyncio.to_thread()`.

### Database

SQLite (`subscribers.db`) with four tables:

- **`decision_points`** — stores each analysis run (ticker, date, verdicts, agent reports, scores, entry/exit prices, deep research fields). 40+ columns with migration history.
- **`decision_tracking`** — tracks price movement post-decision (foreign key to decision_points)
- **`batch_comparisons`** — tracks batch comparison runs across candidates
- **`subscribers`** — email subscription management

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

- **LOO (Limit Order Optimizer):** `docs/proposals/LOO_Implementation_Plan.docx` — 4-phase funnel (Scan -> Validate -> Score -> Present) for monitoring BUY_LIMIT recs approaching entry range
- **Technical Dual-Track:** `docs/proposals/TECHNICAL_DUALTRACK_PROPOSAL.md` — deterministic risk flags from raw TradingView data alongside LLM analysis, replacing fragile string-matching
- **Sell Council (Plan A):** `scripts/reassess_positions.py` — re-runs sensors + deep research with sell-focused prompts for owned positions
- **Sell Price Extension (Plan B):** `docs/proposals/PLAN_sell_price_implementation.md` — extends deep research to output sell_price_low/high/ceiling_exit/exit_trigger at initial analysis time

## Known improvement backlog

Prioritized roughly by impact:

1. **Build backtesting harness** — highest impact missing piece. Replay historical drops through the pipeline and measure recommendation accuracy.
2. **Extend evaluation window** — currently 1 week. Add 2/4/8 week tracking to `decision_tracking`.
3. **Feedback loop** — feed past accuracy data into the PM prompt so the model can calibrate.
4. **Tiered Bollinger gate** — replace flat %B < 0.50 with graduated tiers.
5. **Async rate limiting** — replace any remaining `time.sleep()` with proper async patterns.
6. **Parallel data pre-fetch** — fetch data concurrently for screened candidates instead of sequentially.
7. **Add volume profile analysis** — compare current volume to 20-day average.
8. **Options market data** — IV, put/call ratios, unusual activity as additional signal.
9. **Gradient market regime** — replace binary SPY/SMA200 check with continuous signal.
