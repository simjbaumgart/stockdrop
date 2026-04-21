# Alpaca Symbol Mapping (BRK-B → BRK.B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the Alpaca API rejection of hyphenated share-class tickers (e.g. `BRK-B`) by translating to Alpaca's dot form (`BRK.B`) at the service boundary, while keeping the rest of the codebase using the hyphen form.

**Architecture:** The project uses hyphen form (`BRK-B`) everywhere (Yahoo/TradingView convention) — `stock_metadata`, `stock_tickers`, DB rows, downstream agents. Alpaca is the only consumer that wants the dot form. Fix it at the Alpaca boundary only: translate request symbols on the way in, and translate response keys on the way out so callers still see `BRK-B`. This keeps the leak contained to `alpaca_service.py`.

**Tech Stack:** Python 3.9, `alpaca-py` SDK, pytest.

---

## File Structure

- **Modify:** `app/services/alpaca_service.py` — add a private translation helper and apply it inside `get_snapshots` (symbol list in, dict keys out). `get_latest_price` calls `get_snapshots` so it inherits the fix automatically.
- **Create:** `tests/test_alpaca_service.py` — unit tests covering the symbol translation (forward, reverse, passthrough, multi-dash edge case) and the `get_snapshots` round-trip using a mocked `StockHistoricalDataClient`.

No callers need to change. `stock_service.py` continues to pass/receive `BRK-B`. `stock_metadata` stays as-is.

---

## Task 1: Add a failing test for the symbol translation helpers

**Files:**
- Create: `tests/test_alpaca_service.py`

- [ ] **Step 1: Write the failing test file**

```python
# tests/test_alpaca_service.py
"""Unit tests for AlpacaService symbol translation and snapshot round-trip."""

from unittest.mock import MagicMock, patch

import pytest


# --- Translation helper tests -------------------------------------------------

class TestSymbolTranslation:
    def test_to_alpaca_replaces_single_hyphen(self):
        from app.services.alpaca_service import AlpacaService
        assert AlpacaService._to_alpaca_symbol("BRK-B") == "BRK.B"

    def test_to_alpaca_passthrough_for_plain_symbol(self):
        from app.services.alpaca_service import AlpacaService
        assert AlpacaService._to_alpaca_symbol("AAPL") == "AAPL"

    def test_to_alpaca_replaces_all_hyphens(self):
        """Defensive: if a weird ticker ever has two dashes, translate both."""
        from app.services.alpaca_service import AlpacaService
        assert AlpacaService._to_alpaca_symbol("FOO-BAR-BAZ") == "FOO.BAR.BAZ"

    def test_from_alpaca_replaces_dot(self):
        from app.services.alpaca_service import AlpacaService
        assert AlpacaService._from_alpaca_symbol("BRK.B") == "BRK-B"

    def test_from_alpaca_passthrough_for_plain_symbol(self):
        from app.services.alpaca_service import AlpacaService
        assert AlpacaService._from_alpaca_symbol("AAPL") == "AAPL"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_alpaca_service.py -v`
Expected: FAIL with `AttributeError: type object 'AlpacaService' has no attribute '_to_alpaca_symbol'`.

- [ ] **Step 3: Add the helpers to AlpacaService**

Edit `app/services/alpaca_service.py`. Add these two `staticmethod`s inside the `AlpacaService` class, just above `get_snapshots`:

```python
    @staticmethod
    def _to_alpaca_symbol(symbol: str) -> str:
        """Translate caller symbol (Yahoo/TradingView style, e.g. BRK-B) to
        Alpaca's share-class form (e.g. BRK.B). Alpaca rejects the hyphen form."""
        return symbol.replace("-", ".")

    @staticmethod
    def _from_alpaca_symbol(symbol: str) -> str:
        """Translate Alpaca response symbol (BRK.B) back to the caller form
        (BRK-B) so the rest of the codebase keeps its existing convention."""
        return symbol.replace(".", "-")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_alpaca_service.py::TestSymbolTranslation -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/alpaca_service.py tests/test_alpaca_service.py
git commit -m "feat(alpaca): add symbol translation helpers for share-class tickers"
```

---

## Task 2: Apply translation inside `get_snapshots` (request + response)

**Files:**
- Modify: `app/services/alpaca_service.py` — the `get_snapshots` method (around lines 24–38).
- Modify: `tests/test_alpaca_service.py` — add a round-trip test.

- [ ] **Step 1: Write the failing round-trip test**

Append this class to `tests/test_alpaca_service.py`:

```python
class TestGetSnapshotsSymbolMapping:
    """Alpaca returns snapshots keyed by dot-form (BRK.B). Callers pass and
    expect hyphen-form (BRK-B). Verify both directions are translated."""

    def _make_service_with_mock_client(self):
        from app.services.alpaca_service import AlpacaService
        svc = AlpacaService.__new__(AlpacaService)  # bypass __init__ / env loading
        svc.stock_client = MagicMock()
        svc.option_client = MagicMock()
        return svc

    def test_request_symbols_are_translated_to_alpaca_form(self):
        svc = self._make_service_with_mock_client()
        svc.stock_client.get_stock_snapshot.return_value = {}

        svc.get_snapshots(["AAPL", "BRK-B"])

        # Inspect the StockSnapshotRequest that was built.
        call_args, _ = svc.stock_client.get_stock_snapshot.call_args
        request = call_args[0]
        assert list(request.symbol_or_symbols) == ["AAPL", "BRK.B"]

    def test_response_keys_are_translated_back_to_caller_form(self):
        svc = self._make_service_with_mock_client()
        aapl_snap = MagicMock(name="AAPL_snapshot")
        brk_snap = MagicMock(name="BRK_snapshot")
        svc.stock_client.get_stock_snapshot.return_value = {
            "AAPL": aapl_snap,
            "BRK.B": brk_snap,
        }

        result = svc.get_snapshots(["AAPL", "BRK-B"])

        assert set(result.keys()) == {"AAPL", "BRK-B"}
        assert result["BRK-B"] is brk_snap
        assert result["AAPL"] is aapl_snap

    def test_no_client_returns_empty(self):
        from app.services.alpaca_service import AlpacaService
        svc = AlpacaService.__new__(AlpacaService)
        svc.stock_client = None
        svc.option_client = None
        assert svc.get_snapshots(["BRK-B"]) == {}

    def test_api_exception_returns_empty(self):
        svc = self._make_service_with_mock_client()
        svc.stock_client.get_stock_snapshot.side_effect = RuntimeError("boom")
        assert svc.get_snapshots(["BRK-B"]) == {}
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `pytest tests/test_alpaca_service.py::TestGetSnapshotsSymbolMapping -v`
Expected: `test_request_symbols_are_translated_to_alpaca_form` FAILS (request contains `BRK-B`, not `BRK.B`) and `test_response_keys_are_translated_back_to_caller_form` FAILS (response has key `BRK.B`, not `BRK-B`). The no-client and exception tests should pass already.

- [ ] **Step 3: Modify `get_snapshots` to translate in both directions**

Replace the body of `get_snapshots` in `app/services/alpaca_service.py` with:

```python
    def get_snapshots(self, symbols: List[str]) -> Dict:
        """
        Fetches snapshots for a list of symbols.
        Returns a dictionary where keys are symbols and values are snapshot objects (or dicts).

        Translates caller symbols (e.g. BRK-B) to Alpaca's share-class form (BRK.B)
        for the request, then translates response keys back so callers see the
        original format they passed in.
        """
        if not self.stock_client:
            return {}

        alpaca_symbols = [self._to_alpaca_symbol(s) for s in symbols]

        try:
            request_params = StockSnapshotRequest(symbol_or_symbols=alpaca_symbols)
            snapshots = self.stock_client.get_stock_snapshot(request_params)
            return {self._from_alpaca_symbol(k): v for k, v in snapshots.items()}
        except Exception as e:
            print(f"Error fetching Alpaca snapshots: {e}")
            return {}
```

- [ ] **Step 4: Run all alpaca tests to verify they pass**

Run: `pytest tests/test_alpaca_service.py -v`
Expected: all 9 tests pass (5 translation + 4 round-trip).

- [ ] **Step 5: Run the existing test suite touch-points to make sure nothing regressed**

Run: `pytest tests/test_research_flow.py tests/test_us_only.py tests/test_deduplication_logic.py -v`
Expected: PASS. These tests mock `alpaca_service` entirely, so the internal change should not affect them.

- [ ] **Step 6: Commit**

```bash
git add app/services/alpaca_service.py tests/test_alpaca_service.py
git commit -m "fix(alpaca): translate BRK-B ↔ BRK.B at the Alpaca API boundary"
```

---

## Task 3: End-to-end smoke test against the real Alpaca API

This task confirms that the live Alpaca API now accepts the call for `BRK-B`. It is a manual check, not a committed automated test, because it requires live credentials and network.

**Files:** (none changed)

- [ ] **Step 1: Confirm env has Alpaca credentials**

Run:
```bash
python -c "import os; from dotenv import load_dotenv; load_dotenv(); print('key:', bool(os.getenv('ALPACA_API_KEY')), 'secret:', bool(os.getenv('ALPACA_SECRET_KEY')))"
```
Expected: `key: True secret: True`. If `False`, stop and configure `.env` before proceeding.

- [ ] **Step 2: Call `get_snapshots(["BRK-B", "AAPL"])` against the live API**

Run:
```bash
python -c "
from app.services.alpaca_service import alpaca_service
snaps = alpaca_service.get_snapshots(['BRK-B', 'AAPL'])
print('keys:', sorted(snaps.keys()))
for k, v in snaps.items():
    price = v.latest_trade.price if getattr(v, 'latest_trade', None) else None
    print(f'{k}: latest_trade.price={price}')
"
```
Expected output (prices will vary):
```
keys: ['AAPL', 'BRK-B']
AAPL: latest_trade.price=<float>
BRK-B: latest_trade.price=<float>
```
Expected: NO "Error fetching Alpaca snapshots" line printed.

- [ ] **Step 3: Exercise the caller path via `StockService.get_daily_movers`**

Run:
```bash
python -c "
from app.services.stock_service import StockService
svc = StockService()
# Trim to a small set including BRK-B to keep the call fast.
svc.stock_tickers = ['AAPL', 'BRK-B', 'JPM']
movers = svc.get_daily_movers(threshold=0.0)
print(f'{len(movers)} movers returned; tickers:', [m[\"symbol\"] for m in movers])
"
```
Expected: prints a non-empty mover list including `BRK-B` among the possible tickers and NO Alpaca error line. (The list may be shorter than 3 if some tickers haven't moved — that's fine; the pass criterion is no API error and that `BRK-B` is accepted.)

- [ ] **Step 4: Record the smoke-test result in the commit / PR body**

No file change — note in the PR description that Task 3 Steps 2 and 3 were run against the live API and returned `BRK-B` cleanly on `<today's date>`.

---

## Self-Review Notes

- **Spec coverage:** The bug report is "Alpaca doesn't accept `BRK-B`; map to `BRK.B`." Task 1 adds the translation primitive. Task 2 wires it into the only entry point that talks to Alpaca for share-class tickers (`get_snapshots`; `get_latest_price` delegates to it). Task 3 verifies the fix live.
- **Why only `alpaca_service.py`:** The hyphen form is the project's canonical format (see `stock_metadata` in `app/services/stock_service.py:99`, the `BRK-B` literal in `scripts/analysis/simulate_portfolio.py:19`, and the logs showing `[BRK.B]` only inside news contexts — not in trading flow). Translating only at the Alpaca boundary avoids touching DB rows, agent prompts, and other services.
- **Reverse translation rationale:** callers (e.g. `stock_service.get_stock_details` at line 213 and the loop at line 847) expect the dict keys to match the symbols they passed in. Without the reverse translation, `if symbol not in snapshots` would silently fail for `BRK-B` and `self.stock_metadata.get(symbol, {})` would miss the metadata row.
- **No placeholders:** every step has real code or a real command with expected output.
