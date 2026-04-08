# Screener Overview Console Output

## Summary

Print a formatted overview of stocks returned by the TradingView screener to the console each time `check_large_cap_drops()` runs. This gives visibility into what the API pull returned before the pipeline processes candidates.

## Location

**File:** `app/services/stock_service.py`
**Insert point:** After `get_large_cap_movers()` returns (line ~337), before the CSV save block.

## Output Format

When stocks are found:

```
==================================================
  Screener Results: 7 stocks pulled from markets
==================================================
  Symbol     Price      Drop %    Exchange
  ─────────────────────────────────────────
  AAPL       $142.50    -7.20%    NASDAQ
  TSLA       $185.30    -6.10%    NASDAQ
  NVDA       $410.00    -5.80%    NASDAQ
  SAP        €178.40    -5.50%    XETR
  BABA       ¥85.20     -8.30%    HKEX
  JPM        $152.80    -5.20%    NYSE
  LLY        $620.00    -5.10%    NYSE
==================================================
```

When no stocks are found:

```
==================================================
  Screener Results: 0 stocks pulled from markets
==================================================
```

## Data Source

Each stock dict from `get_large_cap_movers()` contains:
- `symbol` - Ticker symbol (e.g., "AAPL")
- `price` - Current price (float)
- `change_percent` - Drop percentage (negative float)
- `exchange` - Exchange name (e.g., "NASDAQ", "NYSE", "XETR")

## Implementation Details

- Single inline print block (~15 lines of code)
- Uses `print()` to match existing codebase style
- Prints every screener run (every ~20 minutes)
- No new dependencies
- No new methods or classes
- Price shown with `$` prefix (simple, no currency mapping needed)
- Stocks listed in the order returned by the screener (no additional sorting)

## Scope

- **Files changed:** 1 (`app/services/stock_service.py`)
- **Lines added:** ~15
- **Risk:** None (additive console output only)
