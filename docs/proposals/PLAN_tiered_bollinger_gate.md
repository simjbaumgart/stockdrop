# Tiered Bollinger Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat `%B < 0.50` gatekeeper with a graduated 3-tier classification that lets borderline-extended stocks through full research only when the daily drop is large enough to compensate, and tags every decision with its tier so downstream agents and analytics can adapt.

**Architecture:** Add a single classifier (`classify_tier`) inside `GatekeeperService` that returns one of `DEEP_DIP` / `STANDARD_DIP` / `SHALLOW_DIP` / `REJECT`, driven by `%B` and today's drop magnitude. The existing `check_technical_filters` returns this tier inside the `reasons` dict (and a derived `is_valid` boolean for backward compatibility). The tier is persisted on `decision_points` and injected into the Fund Manager prompt's DECISION CONTEXT block so the PM can apply tier-appropriate skepticism.

**Tech Stack:** Python 3.9+, pytest, SQLite (existing schema migration pattern), no new dependencies.

---

## Tier Specification

| Tier | Conditions | Effect |
|---|---|---|
| `DEEP_DIP` | `%B < 0.30` | Approved. PM context: "high-conviction oversold" |
| `STANDARD_DIP` | `0.30 ≤ %B < 0.50` | Approved (current default behavior). PM context: "standard dip" |
| `SHALLOW_DIP` | `0.50 ≤ %B < 0.70` AND `abs(drop_pct) ≥ 8.0` | Approved. PM context: "extended — apply tighter scrutiny" |
| `REJECT` | `%B ≥ 0.70` OR (`0.50 ≤ %B < 0.70` AND `abs(drop_pct) < 8.0`) OR penny-stock fail | Filtered out before research |

**Rationale:**
- `0.30` cutoff for `DEEP_DIP` is the bottom-30% of the Bollinger band — the stock is genuinely oversold relative to its 20-day distribution.
- `0.50` preserves the current "below midline" admission rule.
- `0.70` is the new outer admission boundary, gated by drop magnitude. An 8%+ daily drop on a still-extended stock often reflects a real catalyst (analyst downgrade, guidance cut) worth analyzing, even if statistical mean-reversion alone wouldn't justify it.
- Below 8% drop with `%B ≥ 0.50` is the AAOI/ARM/CRDO pattern from 2026-04-28 — momentum unwind, not a dip — and stays rejected.

---

## File Structure

- **Modify:** `app/services/gatekeeper_service.py` — add tier constants, `classify_tier()`, integrate into `check_technical_filters()`, accept `drop_pct` parameter
- **Modify:** `tests/test_gatekeeper_liquidity.py` — extend with tier classification tests (or split into a new file if it grows past ~150 lines)
- **Modify:** `app/services/stock_service.py:482` — pass `drop_pct` to gatekeeper, log tier on approval, persist tier
- **Modify:** `app/database.py` — add `gatekeeper_tier TEXT` column to `decision_points` migration map
- **Modify:** `app/models/market_state.py` — add `gatekeeper_tier` field
- **Modify:** `app/services/research_service.py:987` — inject tier into Fund Manager prompt DECISION CONTEXT block

---

### Task 1: Tier constants and classifier function

**Files:**
- Modify: `app/services/gatekeeper_service.py`
- Test: `tests/test_gatekeeper_liquidity.py`

- [ ] **Step 1: Write failing tests for `classify_tier`**

Append to `tests/test_gatekeeper_liquidity.py`:

```python
from app.services.gatekeeper_service import (
    GatekeeperService,
    MIN_PRICE_USD,
    TIER_DEEP_DIP,
    TIER_STANDARD_DIP,
    TIER_SHALLOW_DIP,
    TIER_REJECT,
)


def test_classify_tier_deep_dip(gatekeeper):
    assert gatekeeper.classify_tier(pct_b=0.10, drop_pct=-6.0) == TIER_DEEP_DIP
    assert gatekeeper.classify_tier(pct_b=0.29, drop_pct=-5.5) == TIER_DEEP_DIP


def test_classify_tier_standard_dip(gatekeeper):
    assert gatekeeper.classify_tier(pct_b=0.30, drop_pct=-5.5) == TIER_STANDARD_DIP
    assert gatekeeper.classify_tier(pct_b=0.49, drop_pct=-5.5) == TIER_STANDARD_DIP


def test_classify_tier_shallow_dip_requires_8pct_drop(gatekeeper):
    # 0.50 ≤ %B < 0.70 with drop ≥ 8% → admitted as SHALLOW_DIP
    assert gatekeeper.classify_tier(pct_b=0.55, drop_pct=-8.0) == TIER_SHALLOW_DIP
    assert gatekeeper.classify_tier(pct_b=0.69, drop_pct=-12.5) == TIER_SHALLOW_DIP


def test_classify_tier_shallow_zone_with_small_drop_rejects(gatekeeper):
    # 0.50 ≤ %B < 0.70 but drop < 8% → REJECT (the AAOI/CRDO 2026-04-28 case)
    assert gatekeeper.classify_tier(pct_b=0.55, drop_pct=-7.99) == TIER_REJECT
    assert gatekeeper.classify_tier(pct_b=0.62, drop_pct=-5.5) == TIER_REJECT


def test_classify_tier_above_70_always_rejects(gatekeeper):
    # %B ≥ 0.70 → REJECT regardless of drop magnitude (the ARM 2026-04-28 case)
    assert gatekeeper.classify_tier(pct_b=0.70, drop_pct=-15.0) == TIER_REJECT
    assert gatekeeper.classify_tier(pct_b=0.98, drop_pct=-20.0) == TIER_REJECT


def test_classify_tier_handles_positive_drop_pct(gatekeeper):
    # Defensive: caller may pass +8.0 instead of -8.0; classifier uses abs()
    assert gatekeeper.classify_tier(pct_b=0.55, drop_pct=8.0) == TIER_SHALLOW_DIP
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_gatekeeper_liquidity.py -v -k classify_tier`
Expected: 6 FAIL with `ImportError` on `TIER_DEEP_DIP` etc.

- [ ] **Step 3: Add tier constants and classifier**

In `app/services/gatekeeper_service.py`, after the `MIN_PRICE_USD = 5.0` line add:

```python
# Tier names for the graduated Bollinger gate.
TIER_DEEP_DIP = "DEEP_DIP"           # %B < 0.30 — high-conviction oversold
TIER_STANDARD_DIP = "STANDARD_DIP"   # 0.30 ≤ %B < 0.50 — current default
TIER_SHALLOW_DIP = "SHALLOW_DIP"     # 0.50 ≤ %B < 0.70 with ≥ 8% drop
TIER_REJECT = "REJECT"

# Tier boundaries
PCT_B_DEEP = 0.30
PCT_B_STANDARD = 0.50
PCT_B_SHALLOW = 0.70
SHALLOW_MIN_DROP_PCT = 8.0
```

Then add this method to `GatekeeperService` (above `check_technical_filters`):

```python
def classify_tier(self, pct_b: float, drop_pct: float) -> str:
    """
    Classify a candidate into a Bollinger gate tier.

    Args:
        pct_b: Bollinger %B value, (price - lower) / (upper - lower).
        drop_pct: Today's percentage drop. Sign-agnostic (uses abs()).

    Returns one of TIER_DEEP_DIP, TIER_STANDARD_DIP, TIER_SHALLOW_DIP, TIER_REJECT.
    """
    drop_magnitude = abs(drop_pct)
    if pct_b < PCT_B_DEEP:
        return TIER_DEEP_DIP
    if pct_b < PCT_B_STANDARD:
        return TIER_STANDARD_DIP
    if pct_b < PCT_B_SHALLOW and drop_magnitude >= SHALLOW_MIN_DROP_PCT:
        return TIER_SHALLOW_DIP
    return TIER_REJECT
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_gatekeeper_liquidity.py -v -k classify_tier`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/gatekeeper_service.py tests/test_gatekeeper_liquidity.py
git commit -m "feat(gatekeeper): add tier classifier (deep/standard/shallow/reject)"
```

---

### Task 2: Wire tier into `check_technical_filters`

**Files:**
- Modify: `app/services/gatekeeper_service.py`
- Test: `tests/test_gatekeeper_liquidity.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_gatekeeper_liquidity.py`:

```python
def test_check_technical_filters_returns_tier_deep_dip(gatekeeper):
    cached = {"close": 42.0, "bb_lower": 40.0, "bb_upper": 100.0, "volume": 2_000_000}
    # %B = (42-40)/(100-40) = 0.033 → DEEP_DIP
    is_valid, reasons = gatekeeper.check_technical_filters(
        symbol="AAPL", cached_indicators=cached, drop_pct=-6.5
    )
    assert is_valid is True
    assert reasons["tier"] == TIER_DEEP_DIP


def test_check_technical_filters_returns_tier_shallow_when_drop_large(gatekeeper):
    cached = {"close": 50.0, "bb_lower": 40.0, "bb_upper": 60.0, "volume": 2_000_000}
    # %B = 0.5 — borderline shallow
    is_valid, reasons = gatekeeper.check_technical_filters(
        symbol="MSFT", cached_indicators=cached, drop_pct=-9.0
    )
    assert is_valid is True
    assert reasons["tier"] == TIER_SHALLOW_DIP


def test_check_technical_filters_rejects_shallow_zone_with_small_drop(gatekeeper):
    cached = {"close": 50.0, "bb_lower": 40.0, "bb_upper": 60.0, "volume": 2_000_000}
    is_valid, reasons = gatekeeper.check_technical_filters(
        symbol="MSFT", cached_indicators=cached, drop_pct=-5.0
    )
    assert is_valid is False
    assert reasons["tier"] == TIER_REJECT


def test_check_technical_filters_drop_pct_optional_defaults_to_zero(gatekeeper):
    """Backward-compat: callers that don't supply drop_pct still work; shallow zone rejects."""
    cached = {"close": 50.0, "bb_lower": 40.0, "bb_upper": 60.0, "volume": 2_000_000}
    is_valid, reasons = gatekeeper.check_technical_filters(
        symbol="MSFT", cached_indicators=cached
    )
    assert is_valid is False
    assert reasons["tier"] == TIER_REJECT
```

- [ ] **Step 2: Update one existing test to assert tier is present**

Replace `test_check_technical_filters_accepts_above_5_with_dip` body with:

```python
def test_check_technical_filters_accepts_above_5_with_dip(gatekeeper):
    cached = {
        "close": 42.00,
        "bb_lower": 40.00,
        "bb_upper": 60.00,
        "volume": 2_000_000,
    }
    is_valid, reasons = gatekeeper.check_technical_filters(
        symbol="AAPL", cached_indicators=cached, drop_pct=-6.0
    )
    # %B = (42-40)/(60-40) = 0.10 -> DEEP_DIP, qualifies
    assert is_valid is True
    assert "liquidity_status" in reasons
    assert "bb_status" in reasons
    assert reasons["tier"] == TIER_DEEP_DIP
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_gatekeeper_liquidity.py -v`
Expected: 4 new FAIL with `TypeError: unexpected keyword argument 'drop_pct'` or KeyError on `tier`.

- [ ] **Step 4: Update `check_technical_filters` signature and body**

In `app/services/gatekeeper_service.py`, replace the `check_technical_filters` method signature and Bollinger block:

```python
def check_technical_filters(
    self,
    symbol: str,
    region: str = "US",
    exchange: str = None,
    screener: str = None,
    cached_indicators: Dict = None,
    drop_pct: float = 0.0,
) -> Tuple[bool, Dict]:
    """
    Applies the tiered Bollinger gate plus liquidity pre-filter.
    Returns (is_valid, reasons_dict). reasons['tier'] is always set.
    """
    try:
        if cached_indicators:
            indicators = cached_indicators
        else:
            indicators = tradingview_service.get_technical_indicators(
                symbol, region=region, exchange=exchange, screener=screener
            )

        if not indicators:
            return False, {"error": "Insufficient data", "tier": TIER_REJECT}

        reasons = {}
        price = indicators.get("close", 0.0)
        bb_lower = indicators.get("bb_lower", 0.0)
        bb_upper = indicators.get("bb_upper", 0.0)

        # --- Pre-filter: liquidity ---
        liquidity_ok, liquidity_reason = self.check_liquidity_filter(price)
        reasons["liquidity_status"] = liquidity_reason
        reasons["price"] = price
        if not liquidity_ok:
            reasons["lower_bb"] = bb_lower
            reasons["tier"] = TIER_REJECT
            return False, reasons

        # --- Bollinger %B ---
        if bb_upper != bb_lower:
            curr_pct_b = (price - bb_lower) / (bb_upper - bb_lower)
        else:
            curr_pct_b = 0.5

        tier = self.classify_tier(pct_b=curr_pct_b, drop_pct=drop_pct)
        is_valid = tier != TIER_REJECT

        if tier == TIER_DEEP_DIP:
            reasons["bb_status"] = f"%B ({curr_pct_b:.2f}) < {PCT_B_DEEP:.2f} (Deep Dip)"
        elif tier == TIER_STANDARD_DIP:
            reasons["bb_status"] = f"%B ({curr_pct_b:.2f}) < {PCT_B_STANDARD:.2f} (Standard Dip)"
        elif tier == TIER_SHALLOW_DIP:
            reasons["bb_status"] = (
                f"%B ({curr_pct_b:.2f}) in [{PCT_B_STANDARD:.2f}, {PCT_B_SHALLOW:.2f}) "
                f"with drop {abs(drop_pct):.1f}% >= {SHALLOW_MIN_DROP_PCT:.1f}% (Shallow Dip)"
            )
        else:
            if curr_pct_b >= PCT_B_SHALLOW:
                reasons["bb_status"] = f"%B ({curr_pct_b:.2f}) >= {PCT_B_SHALLOW:.2f} (Not Dip Enough)"
            else:
                reasons["bb_status"] = (
                    f"%B ({curr_pct_b:.2f}) in shallow zone but drop "
                    f"{abs(drop_pct):.1f}% < {SHALLOW_MIN_DROP_PCT:.1f}% (Insufficient Drop)"
                )

        reasons["lower_bb"] = bb_lower
        reasons["bb_pct_b"] = curr_pct_b
        reasons["tier"] = tier
        return is_valid, reasons

    except Exception as e:
        print(f"Error in technical filters for {symbol}: {e}")
        return False, {"error": str(e), "tier": TIER_REJECT}
```

- [ ] **Step 5: Run all gatekeeper tests**

Run: `pytest tests/test_gatekeeper_liquidity.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/gatekeeper_service.py tests/test_gatekeeper_liquidity.py
git commit -m "feat(gatekeeper): tier-aware check_technical_filters with drop_pct input"
```

---

### Task 3: Pass `drop_pct` from stock_service and log tier

**Files:**
- Modify: `app/services/stock_service.py:482`

- [ ] **Step 1: Update gatekeeper call site**

In `app/services/stock_service.py`, locate the block around line 477-516 and:

1. Capture `change_percent` from the `stock` dict before the call (it's already being read further down at line 518 — pull it earlier):

```python
change_percent = stock.get("change_percent", 0.0)
print(f"GATEKEEPER: Checking technical filters for {symbol}...")
region = stock.get("region", "US")
screener = stock.get("screener")
cached_indicators = stock.get("cached_indicators")

is_valid, reasons = gatekeeper_service.check_technical_filters(
    symbol,
    region=region,
    exchange=exchange,
    screener=screener,
    cached_indicators=cached_indicators,
    drop_pct=change_percent,
)
```

2. Update the approval log line (currently line 516) to include the tier:

```python
tier = reasons.get("tier", "UNKNOWN")
print(f"GATEKEEPER: {symbol} APPROVED [tier={tier}]. Reasons: {reasons}")
```

3. Confirm `change_percent` is no longer being re-read from `stock` later in the same iteration — search for its second use and remove the redundant read.

- [ ] **Step 2: Manual smoke test**

Run: `python3 main.py --run-for 5`
Expected console output: at least one approved candidate shows `APPROVED [tier=DEEP_DIP]` or `[tier=STANDARD_DIP]`. Watch for any candidate that previously rejected with `%B (0.55–0.69)` and a drop ≥8%; it should now print `APPROVED [tier=SHALLOW_DIP]`. (CTRL-C after the first cycle finishes.)

- [ ] **Step 3: Commit**

```bash
git add app/services/stock_service.py
git commit -m "feat(stock-service): pipe drop_pct into gatekeeper, log tier on approval"
```

---

### Task 4: Persist tier on `decision_points`

**Files:**
- Modify: `app/database.py`
- Modify: `app/models/market_state.py`
- Modify: `app/services/stock_service.py` (decision-record write site)

- [ ] **Step 1: Add column to schema migration map**

In `app/database.py`, add to the `new_columns` dict (around line 124, near `batch_id`):

```python
"gatekeeper_tier": "TEXT",
```

- [ ] **Step 2: Add field to MarketState**

In `app/models/market_state.py`, add a `gatekeeper_tier: Optional[str] = None` attribute alongside `ticker`, default `None`. (If MarketState is a dataclass, add it as a field with default `None`.)

- [ ] **Step 3: Locate and update the decision-record insert site**

Run:

```bash
grep -n "INSERT INTO decision_points\|decision_points.*INSERT" app/services/stock_service.py app/services/research_service.py
```

Add `gatekeeper_tier` to the INSERT column list and value tuple. Source the value from `reasons.get("tier")` captured in Task 3 (thread it down to the same scope where the INSERT happens, or stash on the MarketState before passing to research).

- [ ] **Step 4: Verify migration applies on next startup**

Run: `python3 -c "from app.database import init_db; init_db()"`
Expected: no error. Then:

```bash
sqlite3 subscribers.db "PRAGMA table_info(decision_points);" | grep gatekeeper_tier
```

Expected output: one row containing `gatekeeper_tier|TEXT`.

- [ ] **Step 5: Run a candidate through the pipeline and verify the value lands**

Run a single-cycle pipeline against a known dipping symbol. Then:

```bash
sqlite3 subscribers.db "SELECT ticker, gatekeeper_tier, datetime(created_at) FROM decision_points ORDER BY id DESC LIMIT 1;"
```

Expected: the latest row shows the tier (e.g. `DEEP_DIP`), not NULL.

- [ ] **Step 6: Commit**

```bash
git add app/database.py app/models/market_state.py app/services/stock_service.py
git commit -m "feat(db): persist gatekeeper_tier on decision_points"
```

---

### Task 5: Inject tier into Fund Manager prompt

**Files:**
- Modify: `app/services/research_service.py:987`

- [ ] **Step 1: Update the PM prompt builder**

In `app/services/research_service.py`, modify `_create_fund_manager_prompt` to add a tier-context line in the DECISION CONTEXT block. After:

```python
- This is a "Buy the Dip" evaluation. We are looking for oversold large-cap stocks with recovery potential.
- The investor holds positions until recovery (weeks to months), not day-trading.
```

Add (before `RISK FACTORS`):

```python
- Gatekeeper Tier: {tier_line}
```

And construct `tier_line` at the top of the function:

```python
tier = getattr(state, "gatekeeper_tier", None)
tier_line = {
    "DEEP_DIP": "DEEP_DIP — %B < 0.30, statistically oversold. Default toward action if fundamentals support.",
    "STANDARD_DIP": "STANDARD_DIP — %B in [0.30, 0.50). Standard dip-buying setup; weigh fundamentals normally.",
    "SHALLOW_DIP": (
        "SHALLOW_DIP — %B in [0.50, 0.70). Stock is still extended above its 20-day midline; "
        "admitted only because today's drop was large. Apply tighter scrutiny: require a clear recovery "
        "catalyst and tighter stop-loss. Default toward WAIT_FOR_STAB or PASS unless the bull case is strong."
    ),
}.get(tier, "UNKNOWN — gatekeeper tier missing; treat as STANDARD_DIP.")
```

Then insert `tier_line=tier_line` into the `f""""""` formatting context where shown above.

- [ ] **Step 2: Verify prompt renders**

Run an interactive Python session:

```python
from app.models.market_state import MarketState
from app.services.research_service import research_service  # or whichever singleton
state = MarketState(ticker="TEST")
state.gatekeeper_tier = "SHALLOW_DIP"
state.reports = {"bull": "x", "bear": "y", "risk": "z", "technical": ""}
print(research_service._create_fund_manager_prompt(state, [], [], "-9.5%"))
```

Expected: printed prompt contains the line `- Gatekeeper Tier: SHALLOW_DIP — %B in [0.50, 0.70)…`.

- [ ] **Step 3: Commit**

```bash
git add app/services/research_service.py
git commit -m "feat(pm): surface gatekeeper tier in Fund Manager prompt"
```

---

### Task 6: End-to-end smoke + retro check

**Files:** none (validation only)

- [ ] **Step 1: Run a full cycle**

Run: `python3 main.py --run-for 30`
Wait for at least one full screener cycle to complete (look for `Cycle completed`).

- [ ] **Step 2: Inspect the run for tier diversity**

```bash
sqlite3 subscribers.db "SELECT gatekeeper_tier, COUNT(*) FROM decision_points WHERE created_at >= datetime('now','-1 hour') GROUP BY gatekeeper_tier;"
```

Expected: at least one tier label present, `REJECT` rows absent (rejects don't create decision_points).

- [ ] **Step 3: Spot-check a SHALLOW_DIP decision**

If the run produced any `SHALLOW_DIP` row, open its decision JSON / report and confirm the PM's reasoning references the "extended" / "tighter scrutiny" framing. If the PM ignored the tier signal, refine the prompt wording in Task 5.

- [ ] **Step 4: Final commit (if prompt was tweaked)**

Only if Step 3 prompted a refinement.

```bash
git add app/services/research_service.py
git commit -m "fix(pm): tighten SHALLOW_DIP guidance after first live run"
```

---

## Out of scope (follow-ups)

- Dashboard surfacing of tier (templates show ticker/recommendation but not tier yet).
- Tier-conditional deep-research priority (e.g. `DEEP_DIP` jumps the queue). Defer until live data shows it's needed.
- Backtest harness using historic tier labels — depends on backlog item #1.
- Per-tier accuracy reporting in `performance_service.py` — wait for ≥30 decisions per tier before measuring.

## Rollback

The change is additive: `gatekeeper_tier` defaults to NULL on existing rows. To revert behavior to the flat 0.50 cutoff, set `PCT_B_SHALLOW = PCT_B_STANDARD` (drops `SHALLOW_DIP` admissions) without removing any code or column.
