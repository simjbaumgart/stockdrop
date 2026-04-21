# WSB Daily Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fetch Wall Street Breakfast from Seeking Alpha API once per day instead of once per screener run (~72 fetches/day reduced to 1).

**Architecture:** Add a `_get_or_fetch_wsb()` method that checks the daily cache file (`data/wall_street_breakfast/raw_YYYY-MM-DD.json`) before hitting the API. Both `get_evidence()` and `get_counts()` call this single method instead of independently checking `agent_context.json`. The existing `_get_or_create_wsb_cache()` (cleaned content cache) stays as-is and sits downstream.

**Tech Stack:** Python, JSON file caching, existing SeekingAlphaService

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `app/services/seeking_alpha_service.py` | Modify | Add `_get_or_fetch_wsb()`, update `get_evidence()` and `get_counts()` |
| `tests/test_wsb_daily_cache.py` | Create | Tests for the daily cache logic |

---

### Task 1: Add `_get_or_fetch_wsb()` with daily raw cache

**Files:**
- Create: `tests/test_wsb_daily_cache.py`
- Modify: `app/services/seeking_alpha_service.py:423` (insert new method before `_get_or_create_wsb_cache`)

- [ ] **Step 1: Write the failing test**

```python
"""
Tests for WSB daily caching.
Verifies that WSB is fetched from API at most once per day.
"""
import os
import sys
import json
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime

sys.path.append(os.getcwd())


class TestWSBDailyCache(unittest.TestCase):

    def _make_service(self):
        """Create a SeekingAlphaService with mocked external deps."""
        with patch.dict(os.environ, {
            "RAPIDAPI_KEY_SEEKING_ALPHA": "test-key",
            "GEMINI_API_KEY": "test-key",
        }), patch("app.services.seeking_alpha_service.genai"):
            from app.services.seeking_alpha_service import SeekingAlphaService
            return SeekingAlphaService()

    @patch("app.services.seeking_alpha_service.os.path.exists")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    @patch("app.services.seeking_alpha_service.os.makedirs")
    def test_returns_cached_raw_wsb_if_file_exists(self, mock_makedirs, mock_open, mock_exists):
        """If raw_YYYY-MM-DD.json exists, load it and skip API."""
        svc = self._make_service()
        svc.fetch_wall_street_breakfast = MagicMock()  # should NOT be called

        cached_data = [{"title": "Cached WSB", "publishOn": "2026-04-09", "content": "cached"}]
        mock_exists.return_value = True
        mock_open.return_value.read.return_value = json.dumps(cached_data)
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)

        result = svc._get_or_fetch_wsb()

        svc.fetch_wall_street_breakfast.assert_not_called()
        assert result == cached_data

    def test_fetches_and_caches_when_no_file(self):
        """If no raw cache file for today, fetch from API and save."""
        svc = self._make_service()
        fresh_data = [{"title": "Fresh WSB", "publishOn": "2026-04-09", "content": "fresh"}]
        svc.fetch_wall_street_breakfast = MagicMock(return_value=fresh_data)

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = os.path.join(tmpdir, "wall_street_breakfast")
            with patch.object(svc, '_wsb_cache_dir', cache_dir):
                result = svc._get_or_fetch_wsb()

                svc.fetch_wall_street_breakfast.assert_called_once()
                assert result == fresh_data

                # Verify file was written
                today_str = datetime.now().strftime("%Y-%m-%d")
                cache_file = os.path.join(cache_dir, f"raw_{today_str}.json")
                assert os.path.exists(cache_file)
                with open(cache_file) as f:
                    assert json.load(f) == fresh_data

    def test_returns_empty_list_when_no_key(self):
        """If no API key, return empty list without fetching."""
        with patch.dict(os.environ, {}, clear=True), \
             patch("app.services.seeking_alpha_service.genai"):
            from app.services.seeking_alpha_service import SeekingAlphaService
            svc = SeekingAlphaService()
            result = svc._get_or_fetch_wsb()
            assert result == []

    def test_returns_empty_list_when_api_returns_nothing(self):
        """If API returns empty, return empty and don't cache."""
        svc = self._make_service()
        svc.fetch_wall_street_breakfast = MagicMock(return_value=[])

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = os.path.join(tmpdir, "wall_street_breakfast")
            with patch.object(svc, '_wsb_cache_dir', cache_dir):
                result = svc._get_or_fetch_wsb()

                assert result == []
                # Should NOT create cache file for empty data
                assert not os.path.exists(cache_dir)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_wsb_daily_cache.py -v`
Expected: FAIL — `AttributeError: 'SeekingAlphaService' object has no attribute '_get_or_fetch_wsb'`

- [ ] **Step 3: Write the implementation**

Add `_wsb_cache_dir` property and `_get_or_fetch_wsb()` method to `SeekingAlphaService` in `app/services/seeking_alpha_service.py`. Insert after `__init__` (around line 29), before `_call_endpoint`:

```python
    @property
    def _wsb_cache_dir(self):
        return "data/wall_street_breakfast"

    @_wsb_cache_dir.setter
    def _wsb_cache_dir(self, value):
        self.__wsb_cache_dir = value

    @property
    def _wsb_cache_dir(self):
        return getattr(self, '__wsb_cache_dir', "data/wall_street_breakfast")
```

Actually simpler — just use an instance variable. Add to `__init__` after `self.flash_model = None` block (line 27):

```python
        self.wsb_cache_dir = "data/wall_street_breakfast"
```

Then add the new method before `_get_or_create_wsb_cache` (before line 423):

```python
    def _get_or_fetch_wsb(self) -> List[Dict]:
        """
        Returns raw WSB data, fetching from API at most once per day.
        Checks for data/wall_street_breakfast/raw_YYYY-MM-DD.json first.
        """
        today_str = datetime.now().strftime("%Y-%m-%d")
        cache_file = os.path.join(self.wsb_cache_dir, f"raw_{today_str}.json")

        # 1. Try daily cache
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r") as f:
                    items = json.load(f)
                if items:
                    return items
            except Exception as e:
                logger.error(f"Error reading raw WSB cache: {e}")

        # 2. Fetch from API (once per day)
        if not self.rapidapi_key:
            return []

        logger.info("Fetching WSB from API (daily)...")
        items = self.fetch_wall_street_breakfast()

        # 3. Save raw cache (only if we got data)
        if items:
            try:
                os.makedirs(self.wsb_cache_dir, exist_ok=True)
                with open(cache_file, "w") as f:
                    json.dump(items, f, indent=2)
                print(f"  > [Seeking Alpha Service] Cached raw WSB to {cache_file}")
            except Exception as e:
                logger.error(f"Error saving raw WSB cache: {e}")

        return items
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_wsb_daily_cache.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_wsb_daily_cache.py app/services/seeking_alpha_service.py
git commit -m "feat: add _get_or_fetch_wsb with daily raw cache"
```

---

### Task 2: Wire `get_evidence()` to use `_get_or_fetch_wsb()`

**Files:**
- Modify: `app/services/seeking_alpha_service.py:222-228` (the WSB section in `get_evidence`)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_wsb_daily_cache.py`:

```python
    @patch("app.services.seeking_alpha_service.os.path.exists")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    def test_get_evidence_uses_daily_cache(self, mock_open, mock_exists):
        """get_evidence should call _get_or_fetch_wsb instead of checking agent_context.json for WSB."""
        svc = self._make_service()

        # Mock _get_or_fetch_wsb to return WSB data
        wsb_data = [{"title": "Test WSB", "publishOn": "2026-04-09", "content": "<p>Market rallied.</p>"}]
        svc._get_or_fetch_wsb = MagicMock(return_value=wsb_data)

        # Mock fetch_wall_street_breakfast — should NOT be called directly
        svc.fetch_wall_street_breakfast = MagicMock()

        # Provide stock data via agent_context.json so get_evidence doesn't bail early
        stock_data = {
            "stocks": {"TEST": {"analysis": [], "news": [], "press_releases": []}},
            "wall_street_breakfast": []  # Empty here — should come from _get_or_fetch_wsb
        }
        mock_exists.return_value = True
        mock_open.return_value.read.return_value = json.dumps(stock_data)
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)

        result = svc.get_evidence("TEST")

        svc._get_or_fetch_wsb.assert_called_once()
        svc.fetch_wall_street_breakfast.assert_not_called()
        assert "Wall Street Breakfast" in result or "WALL STREET BREAKFAST" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_wsb_daily_cache.py::TestWSBDailyCache::test_get_evidence_uses_daily_cache -v`
Expected: FAIL — `_get_or_fetch_wsb` not called, `fetch_wall_street_breakfast` called instead

- [ ] **Step 3: Update `get_evidence()` WSB section**

In `app/services/seeking_alpha_service.py`, replace lines 222-228:

```python
            # 3. Check WSB (Fetcher)
            wsb_items = data.get("wall_street_breakfast", [])
            if not wsb_items and self.rapidapi_key:
                 logger.info("WSB data missing. Fetching...")
                 wsb_items = self.fetch_wall_street_breakfast()
                 if wsb_items:
                     self._save_fetched_data(None, wsb_items, type="wsb")
                     # No need to reload full context, just use local var
```

With:

```python
            # 3. Get WSB via daily cache (fetches from API at most once/day)
            wsb_items = self._get_or_fetch_wsb()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_wsb_daily_cache.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/seeking_alpha_service.py tests/test_wsb_daily_cache.py
git commit -m "refactor: get_evidence uses _get_or_fetch_wsb daily cache"
```

---

### Task 3: Wire `get_counts()` to use `_get_or_fetch_wsb()`

**Files:**
- Modify: `app/services/seeking_alpha_service.py:396-406` (the WSB section in `get_counts`)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_wsb_daily_cache.py`:

```python
    @patch("app.services.seeking_alpha_service.os.path.exists")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    def test_get_counts_uses_daily_cache(self, mock_open, mock_exists):
        """get_counts should use _get_or_fetch_wsb for WSB counts."""
        svc = self._make_service()

        wsb_data = [{"title": "Test WSB", "publishOn": "2026-04-09T07:30:00-04:00", "content": "text"}]
        svc._get_or_fetch_wsb = MagicMock(return_value=wsb_data)
        svc.fetch_wall_street_breakfast = MagicMock()

        # Stock data in agent_context.json
        context = {
            "stocks": {"TEST": {"analysis": [1, 2], "news": [1], "press_releases": []}},
            "wall_street_breakfast": []
        }
        mock_exists.return_value = True
        mock_open.return_value.read.return_value = json.dumps(context)
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)

        counts = svc.get_counts("TEST")

        svc._get_or_fetch_wsb.assert_called_once()
        assert counts["wsb"] == 1
        assert counts["wsb_date"] == "2026-04-09"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_wsb_daily_cache.py::TestWSBDailyCache::test_get_counts_uses_daily_cache -v`
Expected: FAIL — `_get_or_fetch_wsb` not called

- [ ] **Step 3: Update `get_counts()` WSB section**

In `app/services/seeking_alpha_service.py`, replace lines 396-406:

```python
            # WSB is global, so it technically exists for all, but let's count it
            wsb_items = data.get("wall_street_breakfast", [])
            wsb_count = len(wsb_items)
            wsb_date = "N/A"
            if wsb_items:
                # Try to get date from first item
                raw_date = wsb_items[0].get("publishOn", "")
                if raw_date:
                    # Keep it simple or format it. Raw is usually ISO-like or date string.
                    # Just taking the date part if possible (YYYY-MM-DD)
                    wsb_date = raw_date.split("T")[0]
```

With:

```python
            # WSB via daily cache (fetches from API at most once/day)
            wsb_items = self._get_or_fetch_wsb()
            wsb_count = len(wsb_items)
            wsb_date = "N/A"
            if wsb_items:
                raw_date = wsb_items[0].get("publishOn", "")
                if raw_date:
                    wsb_date = raw_date.split("T")[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_wsb_daily_cache.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/seeking_alpha_service.py tests/test_wsb_daily_cache.py
git commit -m "refactor: get_counts uses _get_or_fetch_wsb daily cache"
```

---

### Task 4: Run live test to verify end-to-end

**Files:**
- Run: `tests/test_seeking_alpha_live.py` (existing)

- [ ] **Step 1: Run the live API test**

Run: `python3 tests/test_seeking_alpha_live.py`
Expected: All tests pass, WSB test still works, no regressions

- [ ] **Step 2: Verify daily cache file was created**

Run: `ls -la data/wall_street_breakfast/`
Expected: See both `raw_2026-04-09.json` (new) and `processed_2026-04-09.json` (existing cleaned cache)

- [ ] **Step 3: Run live test again — verify no second API call**

Run: `python3 tests/test_seeking_alpha_live.py`
Expected: WSB loads from cache. Console should NOT show "Fetching FRESH Wall Street Breakfast" — instead shows "Cached raw WSB" or loads silently.

- [ ] **Step 4: Commit**

```bash
git commit --allow-empty -m "test: verified WSB daily cache end-to-end"
```
