"""Console visualization mode for StockDrop.

Triggered by `python main.py --visualization`. Prints rich performance tables
and plotext line charts to the terminal, then the caller exits.

Output 1 (tables) reuses scripts/analysis/verdict_performance.py verbatim.
Output 2 (charts) plots equal-weight cumulative-basket returns vs an SPY
buy-and-hold reference, entering each position at its DB price_at_decision.

Downloaded Yahoo prices are cached to ``data/price_cache.pkl`` (gitignored) and
reused within the same calendar day, so repeated runs don't re-download. Pass
``refresh=True`` (CLI: ``--refresh-prices``) to force a fresh download.
"""

from __future__ import annotations

import os
import pickle
import re
import warnings
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

# yfinance on pandas 2.3+ emits a Timestamp.utcnow deprecation warning once per
# downloaded ticker — hundreds of lines of noise in this console report. Silence it.
warnings.filterwarnings("ignore", message=r".*Timestamp\.utcnow is deprecated.*")

from scripts.analysis.verdict_performance import (
    BENCHMARK,
    INTENT_LABEL,
    INTENT_ORDER,
    ROI_CLIP,
    build_table,
    fetch_prices,
    load_decisions,
    render_console,
)

WINDOWS: List[int] = [2, 4, 12]
MIN_N: int = 3  # match verdict_performance.py default

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CACHE_PATH = os.path.join(_ROOT, "data", "price_cache.pkl")


def _load_cache() -> Optional[dict]:
    """Load the on-disk price cache, or None if absent/unreadable."""
    if not os.path.exists(CACHE_PATH):
        return None
    try:
        with open(CACHE_PATH, "rb") as fh:
            return pickle.load(fh)
    except Exception:  # noqa: BLE001 — a corrupt cache should never crash the report
        return None


def _save_cache(as_of: str, start_iso: str, prices: Dict[str, pd.Series],
                failed: set) -> None:
    """Persist the merged price set atomically (tmp file + os.replace)."""
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    obj = {
        "as_of": as_of,
        "start": start_iso,
        "prices": pd.DataFrame(prices),  # aligns Series on the union of dates
        "failed": sorted(failed),
    }
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "wb") as fh:
        pickle.dump(obj, fh)
    os.replace(tmp, CACHE_PATH)


def fetch_prices_cached(symbols, start, end, refresh: bool = False) -> Dict[str, pd.Series]:
    """`fetch_prices` with a same-day disk cache keyed on the download date.

    The cache (``data/price_cache.pkl``) is reused as long as it was written today
    and covers the requested start date. Symbols already present are served from
    cache; only genuinely new tickers are downloaded and merged in. Tickers that
    failed to download today are remembered (negative cache) so the ~500 delisted/
    foreign symbols aren't retried every run. A new day, a wider date range, or
    ``refresh=True`` triggers a full re-download.
    """
    needed = sorted(set(symbols) | {BENCHMARK})
    today = date.today().isoformat()
    start_norm = pd.Timestamp(start).normalize()

    cache = None if refresh else _load_cache()
    cache_ok = (
        cache is not None
        and cache.get("as_of") == today
        and pd.Timestamp(cache.get("start")) <= start_norm
    )

    if cache_ok:
        prices = {c: cache["prices"][c].dropna() for c in cache["prices"].columns}
        failed = set(cache.get("failed", []))
        have = set(prices)
        new = [s for s in needed if s not in have and s not in failed]
        if not new:
            print(f"Using today's cached prices — {len(have)} symbols, no download needed "
                  f"({CACHE_PATH}).")
            return {s: prices[s] for s in needed if s in prices}
        print(f"Price cache is fresh; downloading {len(new)} new symbol(s)...")
        # cache["start"] is an ISO string; fetch_prices needs a datetime-like.
        fetched = fetch_prices(new, pd.Timestamp(cache["start"]), end)
        prices.update(fetched)
        failed |= set(new) - set(fetched)
        _save_cache(today, cache["start"], prices, failed)
        return {s: prices[s] for s in needed if s in prices}

    if refresh:
        print("Refreshing price cache (full re-download)...")
    elif cache is not None:
        print("Price cache is stale (new day or wider range) — re-downloading...")
    fetched = fetch_prices(needed, start, end)
    failed = set(needed) - set(fetched)
    _save_cache(today, start_norm.isoformat(), fetched, failed)
    print(f"Cached {len(fetched)} symbols to {CACHE_PATH}.")
    return {s: fetched[s] for s in needed if s in fetched}


def parse_since(spec: str) -> datetime:
    """Parse a --since value into a cutoff datetime.

    Accepts a relative window — ``<n>w`` / ``<n>d`` / ``<n>m`` / ``<n>y`` (weeks,
    days, months, years ago; a space before the unit is allowed) — or an absolute
    date string such as ``2026-04-09``. Raises ValueError on anything unparseable.
    """
    s = spec.strip().lower()
    m = re.fullmatch(r"(\d+)\s*([wdmy])", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        now = datetime.now()
        if unit == "w":
            return now - timedelta(weeks=n)
        if unit == "d":
            return now - timedelta(days=n)
        if unit == "m":
            return (pd.Timestamp(now) - pd.DateOffset(months=n)).to_pydatetime()
        return (pd.Timestamp(now) - pd.DateOffset(years=n)).to_pydatetime()  # "y"
    try:
        return pd.to_datetime(spec).to_pydatetime()
    except Exception as exc:  # noqa: BLE001 — re-raise as a clear user-facing error
        raise ValueError(
            f"Could not parse --since value {spec!r}. "
            "Use a window like '4w', '30d', '3m', '1y', or a date like '2026-04-09'."
        ) from exc


def build_basket_curves(
    df: pd.DataFrame, prices: Dict[str, pd.Series], spy: pd.Series, intent_col: str
) -> dict:
    """Equal-weight cumulative-basket return per intent bucket over calendar time.

    At date t, a bucket's value = mean over positions already entered by t of
    clip(close_t / entry_price, 1±ROI_CLIP); plotted as (value-1)*100. entry_price
    is the row's price_at_decision; close_t is the yfinance close as-of t. The SPY
    reference is buy-and-hold normalized at the chart's start date.
    """
    axis = spy.index  # trading days we have benchmark prices for
    bucket_positions: Dict[str, list] = {}
    earliest = None

    for intent in INTENT_ORDER:
        sub = df[df[intent_col] == intent]
        positions = []
        for _, r in sub.iterrows():
            s = prices.get(r["symbol"])
            try:
                entry_price = float(r["price_at_decision"])
            except (TypeError, ValueError):
                entry_price = float("nan")
            if s is None or not (entry_price > 0):
                continue
            entry_ts = pd.Timestamp(r["date"]).normalize()
            positions.append((entry_ts, entry_price, s))
            if earliest is None or entry_ts < earliest:
                earliest = entry_ts
        if positions:
            bucket_positions[intent] = positions

    if not bucket_positions or earliest is None:
        return {"curves": {}, "spy_dates": [], "spy_vals": []}

    axis = axis[axis >= earliest]

    curves: Dict[str, dict] = {}
    for intent, positions in bucket_positions.items():
        cols = {}
        for i, (entry_ts, entry_price, s) in enumerate(positions):
            reindexed = s.reindex(axis).ffill()
            ratio = (reindexed / entry_price).clip(
                lower=1.0 - ROI_CLIP, upper=1.0 + ROI_CLIP
            )
            ratio[axis < entry_ts] = float("nan")  # not entered yet
            cols[i] = ratio
        mat = pd.DataFrame(cols, index=axis)
        counts = mat.count(axis=1)
        basket = mat.mean(axis=1)
        mask = counts > 0
        dates = list(axis[mask])
        if not dates:
            continue
        vals = list(((basket[mask] - 1.0) * 100.0).values)
        curves[intent] = {
            "dates": dates,
            "vals": vals,
            "final_n": int(counts[mask].iloc[-1]),
        }

    spy_axis = spy.reindex(axis).ffill()
    spy_start = float(spy_axis.iloc[0])
    spy_dates = list(axis)
    if not (spy_start > 0):
        spy_vals = [float("nan")] * len(spy_axis)
    else:
        spy_vals = list(((spy_axis / spy_start - 1.0) * 100.0).values)

    return {"curves": curves, "spy_dates": spy_dates, "spy_vals": spy_vals}


def render_basket_chart(title: str, payload: dict) -> None:
    """Draw cumulative-return lines for each bucket + an SPY reference, in-terminal.

    plotext has no true dashed style, so the SPY reference is distinguished by a
    distinct marker and an explicit '(buy & hold ref)' label.
    """
    import plotext as plt

    curves = payload.get("curves", {})
    if not curves:
        print(f"\n{title}: no data to chart.")
        return

    plt.clear_figure()
    plt.date_form("Y-m-d")
    plt.theme("pro")

    for intent in INTENT_ORDER:
        c = curves.get(intent)
        if not c:
            continue
        xs = [d.strftime("%Y-%m-%d") for d in c["dates"]]
        plt.plot(xs, c["vals"], label=f"{INTENT_LABEL[intent]} (n={c['final_n']})")

    if payload.get("spy_dates"):
        sxs = [d.strftime("%Y-%m-%d") for d in payload["spy_dates"]]
        plt.plot(sxs, payload["spy_vals"], label="SPY (buy & hold ref)", marker="dot")

    plt.title(title)
    plt.xlabel("Date")
    plt.ylabel("Cumulative return %")
    plt.show()


def run_visualization(since: Optional[str] = None, refresh_prices: bool = False) -> None:
    """One-shot console report: alpha tables + cumulative-return charts.

    If ``since`` is given (a window like '4w'/'30d'/'3m'/'1y' or a date like
    '2026-04-09'), only decisions made on/after that cutoff are included.
    Prices are served from a same-day disk cache; ``refresh_prices=True`` forces
    a fresh download.
    """
    df_all = load_decisions()
    # Always cache prices over the FULL decision history so the cache stays
    # reusable across different --since windows within a day. --since only narrows
    # which decisions are analysed, not how much price history is fetched.
    full_start = df_all["date"].min() - timedelta(days=5)

    df = df_all
    if since:
        cutoff = parse_since(since)
        df = df_all[df_all["date"] >= pd.Timestamp(cutoff)].copy()
        print(f"Filtering to decisions since {cutoff.date()} — {len(df)} of {len(df_all)}.")
        if df.empty:
            print("No decisions in that window — nothing to show.")
            return

    print(
        f"Loaded {len(df)} decisions "
        f"({df['date'].min().date()} -> {df['date'].max().date()})"
    )

    end = datetime.now()
    prices = fetch_prices_cached(df["symbol"].tolist(), full_start, end, refresh=refresh_prices)
    spy = prices.get(BENCHMARK)
    if spy is None:
        print("Could not fetch SPY benchmark — aborting.")
        return
    print(f"Have prices for {len(prices) - 1} / {df['symbol'].nunique()} symbols.\n")

    # ---- OUTPUT 1: alpha-vs-SPY tables (reuse verdict_performance) ----
    council_tbl = build_table(df, prices, spy, WINDOWS, "council_intent")
    dr_tbl = build_table(df, prices, spy, WINDOWS, "dr_intent")
    render_console(
        "COUNCIL / PM verdict — alpha vs SPY (market-on-decision entry)",
        council_tbl, WINDOWS, MIN_N,
    )
    print()
    render_console(
        "DEEP RESEARCH verdict — alpha vs SPY (market-on-decision entry)",
        dr_tbl, WINDOWS, MIN_N,
    )
    print()

    # ---- OUTPUT 2: cumulative-return line charts ----
    pm_payload = build_basket_curves(df, prices, spy, "council_intent")
    render_basket_chart("Council / PM verdict — cumulative return vs SPY", pm_payload)

    dr_df = df[df["dr_intent"] != ""].copy()
    dr_payload = build_basket_curves(dr_df, prices, spy, "dr_intent")
    render_basket_chart("Deep Research verdict — cumulative return vs SPY", dr_payload)

    print("\nFootnotes:")
    print("  * Cumulative basket = equal-weight, market-on-decision entry at price_at_decision,")
    print("    buy-and-hold to today; each position's return clipped at +/-300%.")
    print("  * SPY line = buy & hold from each chart's start date.")
    print("  * The pre-Apr 9 2026 stretch comes from the legacy DB (data/subscribers.db),")
    print("    an earlier regime of the tool.")
    print(f"  * Prices cached at {CACHE_PATH} (reused same-day; --refresh-prices to refetch).")
