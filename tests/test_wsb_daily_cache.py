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

    def test_returns_cached_raw_wsb_if_file_exists(self):
        """If raw_YYYY-MM-DD.json exists, load it and skip API."""
        svc = self._make_service()
        svc.fetch_wall_street_breakfast = MagicMock()  # should NOT be called

        cached_data = [{"title": "Cached WSB", "publishOn": "2026-04-09", "content": "cached"}]

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            svc.wsb_cache_dir = tmpdir
            today_str = datetime.now().strftime("%Y-%m-%d")
            cache_file = os.path.join(tmpdir, f"raw_{today_str}.json")
            with open(cache_file, "w") as f:
                json.dump(cached_data, f)

            result = svc._get_or_fetch_wsb()

            svc.fetch_wall_street_breakfast.assert_not_called()
            self.assertEqual(result, cached_data)

    def test_fetches_and_caches_when_no_file(self):
        """If no raw cache file for today, fetch from API and save."""
        svc = self._make_service()
        fresh_data = [{"title": "Fresh WSB", "publishOn": "2026-04-09", "content": "fresh"}]
        svc.fetch_wall_street_breakfast = MagicMock(return_value=fresh_data)

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = os.path.join(tmpdir, "wall_street_breakfast")
            svc.wsb_cache_dir = cache_dir

            result = svc._get_or_fetch_wsb()

            svc.fetch_wall_street_breakfast.assert_called_once()
            self.assertEqual(result, fresh_data)

            # Verify file was written
            today_str = datetime.now().strftime("%Y-%m-%d")
            cache_file = os.path.join(cache_dir, f"raw_{today_str}.json")
            self.assertTrue(os.path.exists(cache_file))
            with open(cache_file) as f:
                self.assertEqual(json.load(f), fresh_data)

    def test_returns_empty_list_when_no_key(self):
        """If no API key, return empty list without fetching."""
        with patch.dict(os.environ, {}, clear=True), \
             patch("app.services.seeking_alpha_service.genai"):
            from app.services.seeking_alpha_service import SeekingAlphaService
            svc = SeekingAlphaService()
            result = svc._get_or_fetch_wsb()
            self.assertEqual(result, [])

    def test_returns_empty_list_when_api_returns_nothing(self):
        """If API returns empty, return empty and don't cache."""
        svc = self._make_service()
        svc.fetch_wall_street_breakfast = MagicMock(return_value=[])

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = os.path.join(tmpdir, "wall_street_breakfast")
            svc.wsb_cache_dir = cache_dir

            result = svc._get_or_fetch_wsb()

            self.assertEqual(result, [])
            # Should NOT create cache file for empty data
            self.assertFalse(os.path.exists(cache_dir))


    def test_get_evidence_uses_daily_cache(self):
        """get_evidence should call _get_or_fetch_wsb instead of fetching WSB directly."""
        svc = self._make_service()

        # Mock _get_or_fetch_wsb to return WSB data
        wsb_data = [{"title": "Test WSB", "publishOn": "2026-04-09", "content": "<p>Market rallied.</p>"}]
        svc._get_or_fetch_wsb = MagicMock(return_value=wsb_data)

        # Mock fetch_wall_street_breakfast — should NOT be called directly
        svc.fetch_wall_street_breakfast = MagicMock()

        # Provide stock data via a temp agent_context.json
        import tempfile
        stock_data = {
            "stocks": {"TEST": {"analysis": [], "news": [], "press_releases": []}},
            "wall_street_breakfast": []  # Empty here — should come from _get_or_fetch_wsb
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(stock_data, f)
            tmp_path = f.name

        try:
            # Patch the sa_path inside get_evidence
            with patch.object(svc, 'get_evidence', wraps=svc.get_evidence):
                # We need to patch the hardcoded path. Easiest: just call and check mocks.
                # Monkey-patch the path used inside get_evidence
                original = svc.get_evidence

                def patched_get_evidence(ticker):
                    import types
                    # Replace the inner sa_path by patching os.path.exists and open
                    return original(ticker)

                result = svc.get_evidence("TEST")

            svc._get_or_fetch_wsb.assert_called_once()
            svc.fetch_wall_street_breakfast.assert_not_called()
            self.assertIn("WALL STREET BREAKFAST", result)
        finally:
            os.unlink(tmp_path)

    def test_get_counts_uses_daily_cache(self):
        """get_counts should use _get_or_fetch_wsb for WSB counts."""
        svc = self._make_service()

        wsb_data = [{"title": "Test WSB", "publishOn": "2026-04-09T07:30:00-04:00", "content": "text"}]
        svc._get_or_fetch_wsb = MagicMock(return_value=wsb_data)
        svc.fetch_wall_street_breakfast = MagicMock()

        # Stock data in agent_context.json
        import tempfile
        context = {
            "stocks": {"TEST": {"analysis": [{"content": "a"}], "news": [], "press_releases": []}},
            "wall_street_breakfast": []
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(context, f)
            tmp_path = f.name

        try:
            counts = svc.get_counts("TEST")

            svc._get_or_fetch_wsb.assert_called_once()
            self.assertEqual(counts["wsb"], 1)
            self.assertEqual(counts["wsb_date"], "2026-04-09")
        finally:
            os.unlink(tmp_path)


if __name__ == "__main__":
    unittest.main()
