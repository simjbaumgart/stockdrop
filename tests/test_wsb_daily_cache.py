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


if __name__ == "__main__":
    unittest.main()
