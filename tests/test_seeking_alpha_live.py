"""
Live integration test for the Seeking Alpha RapidAPI service.

Calls the REAL API with real tickers to verify:
  1. Endpoints are reachable and return valid JSON
  2. Response structure matches what our code expects
  3. Data quality (titles, content, dates present)
  4. Edge cases (obscure tickers, delisted stocks)

Usage:
  python tests/test_seeking_alpha_live.py

Requires: RAPIDAPI_KEY_SEEKING_ALPHA set in environment (or .env)
"""

import os
import sys
import time
import json
from datetime import datetime

# Ensure app imports work
sys.path.append(os.getcwd())

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from app.services.seeking_alpha_service import SeekingAlphaService

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Mix of large-cap, mid-cap, and a potentially tricky ticker
TEST_TICKERS = ["AAPL", "TSLA", "APA"]

# Track results
results = {"passed": 0, "failed": 0, "skipped": 0, "warnings": 0}


def _pass(msg):
    results["passed"] += 1
    print(f"  [PASS] {msg}")


def _fail(msg):
    results["failed"] += 1
    print(f"  [FAIL] {msg}")


def _warn(msg):
    results["warnings"] += 1
    print(f"  [WARN] {msg}")


def _skip(msg):
    results["skipped"] += 1
    print(f"  [SKIP] {msg}")


# ===========================================================================
# Test 1: Raw endpoint connectivity
# ===========================================================================
def test_raw_endpoint_connectivity(svc):
    """Verify we can hit each endpoint type and get valid JSON back."""
    print("\n" + "=" * 60)
    print("TEST 1: Raw Endpoint Connectivity")
    print("=" * 60)

    endpoints = {
        "analysis/v2/list":            {"id": "AAPL", "size": 1},
        "news/v2/list-by-symbol":      {"id": "AAPL", "size": 1},
        "press-releases/v2/list":      {"id": "AAPL", "size": 1},
        "articles/list-wall-street-breakfast": {"size": 1},
        "auto-complete":               {"term": "AAPL"},
    }

    for endpoint, params in endpoints.items():
        resp = svc._call_endpoint(endpoint, params)
        if resp is not None:
            _pass(f"{endpoint} -> returned data (type: {type(resp).__name__})")
        else:
            _fail(f"{endpoint} -> returned None (empty or error)")
        time.sleep(0.3)  # respect rate limits


# ===========================================================================
# Test 2: List response structure
# ===========================================================================
def test_list_response_structure(svc):
    """Verify list endpoints return {'data': [...]} with items that have 'id'."""
    print("\n" + "=" * 60)
    print("TEST 2: List Response Structure")
    print("=" * 60)

    list_endpoints = [
        ("analysis/v2/list",       {"id": "AAPL", "size": 3}),
        ("news/v2/list-by-symbol", {"id": "AAPL", "size": 3}),
        ("press-releases/v2/list", {"id": "AAPL", "size": 3}),
    ]

    for endpoint, params in list_endpoints:
        resp = svc._call_endpoint(endpoint, params)
        if resp is None:
            _fail(f"{endpoint} -> no response")
            continue

        # Check 'data' key
        if "data" not in resp:
            _fail(f"{endpoint} -> missing 'data' key. Keys: {list(resp.keys())}")
            continue

        items = resp["data"]
        if not isinstance(items, list):
            _warn(f"{endpoint} -> 'data' is {type(items).__name__}, expected list")
            continue

        if len(items) == 0:
            _warn(f"{endpoint} -> 'data' is empty list")
            continue

        _pass(f"{endpoint} -> {len(items)} items returned")

        # Check first item has 'id'
        first = items[0]
        if "id" in first:
            _pass(f"  first item id: {first['id']}")
        else:
            _fail(f"  first item missing 'id'. Keys: {list(first.keys())}")

        time.sleep(0.3)


# ===========================================================================
# Test 3: Detail response structure
# ===========================================================================
def test_detail_response_structure(svc):
    """Fetch a detail for each category and verify attributes (title, content, publishOn)."""
    print("\n" + "=" * 60)
    print("TEST 3: Detail Response Structure")
    print("=" * 60)

    # First get an item ID from each list endpoint
    categories = [
        ("analysis/v2/list",       "analysis/v2/get-details",       {"id": "AAPL", "size": 1}),
        ("news/v2/list-by-symbol", "news/get-details",              {"id": "AAPL", "size": 1}),
        ("press-releases/v2/list", "press-releases/get-details",    {"id": "AAPL", "size": 1}),
    ]

    for list_ep, detail_ep, params in categories:
        # Get list
        list_resp = svc._call_endpoint(list_ep, params)
        if not list_resp or "data" not in list_resp or len(list_resp["data"]) == 0:
            _skip(f"{detail_ep} -> could not get item ID from {list_ep}")
            continue

        item_id = list_resp["data"][0].get("id")
        if not item_id:
            _skip(f"{detail_ep} -> first item has no 'id'")
            continue

        time.sleep(0.3)

        # Get detail
        detail_resp = svc._call_endpoint(detail_ep, {"id": item_id})
        if detail_resp is None:
            _fail(f"{detail_ep} (id={item_id}) -> returned None")
            continue

        # Check structure: data -> attributes -> {title, content, publishOn}
        data = detail_resp.get("data")
        if data is None:
            _fail(f"{detail_ep} -> missing 'data' key. Keys: {list(detail_resp.keys())}")
            continue

        attrs = data.get("attributes", {})
        title = attrs.get("title")
        content = attrs.get("content")
        publish_on = attrs.get("publishOn")

        if title:
            _pass(f"{detail_ep} -> title: \"{title[:60]}...\"")
        else:
            _fail(f"{detail_ep} -> missing title")

        if content and len(content) > 0:
            _pass(f"{detail_ep} -> content length: {len(content)} chars")
        else:
            _warn(f"{detail_ep} -> content is empty or missing")

        if publish_on:
            _pass(f"{detail_ep} -> publishOn: {publish_on}")
        else:
            _warn(f"{detail_ep} -> missing publishOn")

        time.sleep(0.3)


# ===========================================================================
# Test 4: Full ticker fetch (the method our app actually uses)
# ===========================================================================
def test_fetch_data_for_ticker(svc):
    """Call fetch_data_for_ticker() for each test ticker and validate output."""
    print("\n" + "=" * 60)
    print("TEST 4: fetch_data_for_ticker() - Real Tickers")
    print("=" * 60)

    for ticker in TEST_TICKERS:
        print(f"\n  --- {ticker} ---")
        data = svc.fetch_data_for_ticker(ticker)

        # Must return dict with expected keys
        if not isinstance(data, dict):
            _fail(f"{ticker} -> returned {type(data).__name__}, expected dict")
            continue

        for key in ["news", "analysis", "press_releases"]:
            if key not in data:
                _fail(f"{ticker} -> missing key '{key}'")
                continue

            items = data[key]
            count = len(items)

            if count > 0:
                _pass(f"{ticker}/{key}: {count} items")

                # Spot-check first item
                first = items[0]
                title = first.get("title")
                content = first.get("content")
                publish = first.get("publishOn")

                if title:
                    _pass(f"  title: \"{title[:70]}\"")
                else:
                    _warn(f"  first item has no title")

                if content and len(content) > 50:
                    _pass(f"  content: {len(content)} chars")
                elif content:
                    _warn(f"  content very short: {len(content)} chars")
                else:
                    _warn(f"  content is empty/None")

                if publish:
                    _pass(f"  publishOn: {publish}")
                else:
                    _warn(f"  no publishOn date")
            else:
                _warn(f"{ticker}/{key}: 0 items (may be normal for this ticker)")

        time.sleep(1)  # pause between tickers


# ===========================================================================
# Test 5: Wall Street Breakfast
# ===========================================================================
def test_wall_street_breakfast(svc):
    """Fetch WSB and verify structure."""
    print("\n" + "=" * 60)
    print("TEST 5: Wall Street Breakfast")
    print("=" * 60)

    items = svc.fetch_wall_street_breakfast()

    if not isinstance(items, list):
        _fail(f"WSB -> returned {type(items).__name__}, expected list")
        return

    if len(items) == 0:
        _warn("WSB -> empty list (may be outside market hours or weekend)")
        return

    _pass(f"WSB -> {len(items)} item(s)")

    for i, item in enumerate(items):
        title = item.get("title")
        content = item.get("content")
        publish = item.get("publishOn")

        if title:
            _pass(f"  [{i}] title: \"{title[:80]}\"")
        else:
            _warn(f"  [{i}] no title")

        if content and len(content) > 100:
            _pass(f"  [{i}] content: {len(content)} chars")
        else:
            _warn(f"  [{i}] content short or missing ({len(content) if content else 0} chars)")

        if publish:
            _pass(f"  [{i}] publishOn: {publish}")
        else:
            _warn(f"  [{i}] no publishOn")


# ===========================================================================
# Test 6: Symbol ID resolution
# ===========================================================================
def test_symbol_resolution(svc):
    """Verify auto-complete resolves tickers to IDs."""
    print("\n" + "=" * 60)
    print("TEST 6: Symbol ID Resolution (auto-complete)")
    print("=" * 60)

    tickers_to_test = ["AAPL", "TSLA", "APA", "XYZFAKE123"]

    for ticker in tickers_to_test:
        sym_id = svc._get_symbol_id(ticker)
        if ticker == "XYZFAKE123":
            if sym_id is None:
                _pass(f"{ticker} -> correctly returned None for fake ticker")
            else:
                _warn(f"{ticker} -> returned ID {sym_id} for fake ticker (unexpected)")
        else:
            if sym_id is not None:
                _pass(f"{ticker} -> resolved to ID: {sym_id}")
            else:
                _fail(f"{ticker} -> could not resolve")
        time.sleep(0.3)


# ===========================================================================
# Test 7: Empty response handling (the bug we just fixed)
# ===========================================================================
def test_empty_response_handling(svc):
    """Verify our service doesn't crash on endpoints that may return empty bodies."""
    print("\n" + "=" * 60)
    print("TEST 7: Empty/Error Response Handling")
    print("=" * 60)

    # Call detail endpoint with a bogus ID - should return None gracefully
    bogus_ids = ["99999999999", "0", "not-a-real-id"]

    for bogus_id in bogus_ids:
        resp = svc._call_endpoint("news/get-details", {"id": bogus_id})
        if resp is None:
            _pass(f"news/get-details (id={bogus_id}) -> gracefully returned None")
        else:
            # Not necessarily wrong - API might return an error object
            _warn(f"news/get-details (id={bogus_id}) -> returned data: {str(resp)[:100]}")
        time.sleep(0.3)


# ===========================================================================
# Main
# ===========================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("SEEKING ALPHA LIVE API TEST")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Tickers: {TEST_TICKERS}")
    print("=" * 60)

    # Check API key
    api_key = os.getenv("RAPIDAPI_KEY_SEEKING_ALPHA")
    if not api_key:
        print("\n[ABORT] RAPIDAPI_KEY_SEEKING_ALPHA not set. Cannot run live tests.")
        print("Set it in your environment or .env file.")
        sys.exit(1)

    print(f"API Key: {api_key[:6]}...{api_key[-4:]}")

    # Create service instance
    svc = SeekingAlphaService()

    # Run tests
    test_raw_endpoint_connectivity(svc)
    test_list_response_structure(svc)
    test_detail_response_structure(svc)
    test_fetch_data_for_ticker(svc)
    test_wall_street_breakfast(svc)
    test_symbol_resolution(svc)
    test_empty_response_handling(svc)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    total = results["passed"] + results["failed"]
    print(f"  Passed:   {results['passed']}")
    print(f"  Failed:   {results['failed']}")
    print(f"  Warnings: {results['warnings']}")
    print(f"  Skipped:  {results['skipped']}")
    if total > 0:
        print(f"  Rate:     {results['passed']/total*100:.0f}% pass")
    print("=" * 60)

    sys.exit(1 if results["failed"] > 0 else 0)
