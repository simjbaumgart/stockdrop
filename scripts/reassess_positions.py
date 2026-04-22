#!/usr/bin/env python3
"""
Sell Council — Reassess Owned Positions (Plan A)

Re-runs Council 1 sensors (Technical, News, Sentiment, Competitive, Seeking Alpha)
to gather fresh evidence, packages it into a JSON context, and hands it directly
to the Deep Research agent with a sell-focused prompt.

Usage:
  python -m scripts.reassess_positions              # all owned positions
  python -m scripts.reassess_positions AAPL NVDA    # specific tickers

Results write to both the main sell columns and dedicated reassess_* columns.
"""

import argparse
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional

# Add project root to path
sys.path.insert(0, ".")

from app.database import init_db, get_decision_points, get_decision_point, update_decision_point
from app.services.stock_service import stock_service
from app.services.tradingview_service import tradingview_service
from app.services.research_service import research_service
from app.services.seeking_alpha_service import seeking_alpha_service
from app.services.deep_research_service import deep_research_service


def _fmt_price(v) -> str:
    """Format price for display."""
    if v is None:
        return "-"
    try:
        return f"${float(v):.2f}"
    except (TypeError, ValueError):
        return str(v)


def _truncate(s: str, max_len: int) -> str:
    """Truncate string with ellipsis."""
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


def _get_owned_positions() -> List[Dict]:
    """Get all decision points with status='Owned'."""
    all_points = get_decision_points()
    return [d for d in all_points if (d.get("status") or "").upper() == "OWNED"]


def _get_decisions_by_symbols(symbols: List[str]) -> List[Dict]:
    """Get the most recent decision point per symbol (prefer Owned, then any)."""
    all_points = get_decision_points()
    by_symbol = {}
    for d in all_points:
        sym = (d.get("symbol") or "").upper()
        if sym not in symbols:
            continue
        # Prefer existing entry; keep most recent if duplicate
        existing = by_symbol.get(sym)
        if not existing or (d.get("timestamp", "") or "") > (existing.get("timestamp", "") or ""):
            by_symbol[sym] = d
    return list(by_symbol.values())


def _build_raw_data(symbol: str, region: str = "US") -> Dict:
    """Fetch fresh news and technical indicators for a symbol."""
    # News: use stock_service's aggregated news (Benzinga, Alpha Vantage, Finnhub)
    news_items = stock_service.get_aggregated_news(symbol, region=region)

    # Technical indicators: use TradingView
    indicators = tradingview_service.get_technical_indicators(symbol, region=region)
    if not indicators:
        ta = tradingview_service.get_technical_analysis(symbol, region=region)
        indicators = ta.get("indicators", {})

    return {
        "indicators": indicators,
        "news_items": news_items,
        "change_percent": 0.0,  # Not critical for reassessment
        "transcript_text": "",
        "transcript_date": None,
    }


def _run_council1_sensors(ticker: str, raw_data: Dict) -> Dict[str, str]:
    """Run Council 1 sensors in parallel and return reports dict."""
    from app.models.market_state import MarketState

    state = MarketState(ticker=ticker, date=datetime.now().strftime("%Y-%m-%d"))
    drop_str = f"{raw_data.get('change_percent', 0):.2f}%"

    tech_prompt = research_service._create_technical_agent_prompt(state, raw_data, drop_str)
    news_prompt = research_service._create_news_agent_prompt(state, raw_data, drop_str)
    comp_prompt = research_service._create_competitive_agent_prompt(state, drop_str)
    sentiment_prompt = research_service._create_market_sentiment_prompt(state, raw_data)

    def run_agent(name, func, *args):
        try:
            return name, func(*args)
        except Exception as e:
            return name, f"[Error in {name}: {e}]"

    import concurrent.futures

    tech_report = news_report = sentiment_report = comp_report = sa_report = ""

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(
                run_agent, "Technical Agent", research_service._call_agent,
                tech_prompt, "Technical Agent", state
            ): "technical",
            executor.submit(
                run_agent, "News Agent", research_service._call_agent,
                news_prompt, "News Agent", state
            ): "news",
            executor.submit(
                run_agent, "Market Sentiment Agent",
                research_service._call_agent,
                sentiment_prompt, "Market Sentiment Agent", state
            ): "sentiment",
            executor.submit(
                run_agent, "Competitive Landscape Agent", research_service._call_agent,
                comp_prompt, "Competitive Landscape Agent", state
            ): "competitive",
            executor.submit(
                run_agent, "Seeking Alpha Agent",
                seeking_alpha_service.get_evidence,
                state.ticker
            ): "seeking_alpha",
        }
        for future in concurrent.futures.as_completed(futures):
            agent_name, result = future.result()
            if agent_name == "Technical Agent":
                tech_report = result
            elif agent_name == "News Agent":
                news_report = result
            elif agent_name == "Market Sentiment Agent":
                sentiment_report = result
            elif agent_name == "Competitive Landscape Agent":
                comp_report = result
            elif agent_name == "Seeking Alpha Agent":
                sa_report = result

    return {
        "technical": tech_report,
        "news": news_report,
        "market_sentiment": sentiment_report,
        "competitive": comp_report,
        "seeking_alpha": sa_report,
    }


def _reassess_one(symbol: str) -> Optional[int]:
    """Reassess a single owned position. Returns result dict or None on failure."""
    # 1. Get decision from DB (most recent for this symbol)
    decisions = _get_decisions_by_symbols([symbol])
    if not decisions:
        print(f"  [{symbol}] No decision point found in DB. Skipping.")
        return None

    decision = decisions[0]
    decision_id = decision.get("id")
    region = decision.get("region") or "US"
    entry_low = decision.get("entry_price_low") or 0.0
    entry_high = decision.get("entry_price_high") or 0.0
    price_at_decision = decision.get("price_at_decision") or 0.0

    # 2. Fetch fresh data
    raw_data = _build_raw_data(symbol, region)
    fresh_technicals = raw_data.get("indicators", {})
    current_price = float(fresh_technicals.get("close") or 0.0)
    if not current_price and price_at_decision:
        current_price = float(price_at_decision)

    # 3. Run Council 1 sensors
    print(f"  [{symbol}] Running Council 1 sensors...")
    sensor_data = _run_council1_sensors(symbol, raw_data)

    # 4. Compute performance since entry
    if price_at_decision and price_at_decision > 0:
        pct_change = ((current_price - float(price_at_decision)) / float(price_at_decision)) * 100
    else:
        pct_change = 0.0

    # 5. Build Deep Research context
    original = {
        "action": decision.get("recommendation"),
        "conviction": decision.get("conviction"),
        "entry_price_low": entry_low,
        "entry_price_high": entry_high,
        "stop_loss": decision.get("stop_loss"),
        "take_profit_1": decision.get("take_profit_1"),
        "take_profit_2": decision.get("take_profit_2"),
        "sell_price_low": decision.get("sell_price_low"),
        "sell_price_high": decision.get("sell_price_high"),
        "ceiling_exit": decision.get("ceiling_exit"),
        "reason": decision.get("reasoning"),
    }
    context = {
        "original_decision": original,
        "current_price": current_price,
        "performance_since_entry": f"{pct_change:+.2f}%",
        "sensor_reports": sensor_data,
        "technical_data": fresh_technicals,
        "raw_news": raw_data.get("news_items", []),
    }

    # 6. Call Deep Research sell reassessment (synchronous)
    result = deep_research_service.execute_sell_reassessment(
        symbol=symbol,
        context=context,
        decision_id=decision_id,
    )
    if not result:
        print(f"  [{symbol}] Deep Research failed or timed out.")
        return None

    # 7. Apply stop loss logic (only raise, never lower)
    current_stop = decision.get("stop_loss")
    new_stop = result.get("updated_stop_loss")
    if new_stop is not None and current_stop is not None:
        try:
            if float(new_stop) <= float(current_stop):
                new_stop = None
        except (TypeError, ValueError):
            new_stop = None

    # 8. Build update kwargs
    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    reassess_reasoning = (
        (result.get("thesis_reasoning") or "")
        + " "
        + (result.get("action_reasoning") or "")
    ).strip()

    update_kwargs = {
        "sell_price_low": result.get("updated_sell_price_low"),
        "sell_price_high": result.get("updated_sell_price_high"),
        "ceiling_exit": result.get("updated_ceiling_exit"),
        "exit_trigger": result.get("exit_trigger"),
        "reassess_sell_action": result.get("sell_action"),
        "reassess_thesis_status": result.get("thesis_status"),
        "reassess_sell_price_low": result.get("updated_sell_price_low"),
        "reassess_sell_price_high": result.get("updated_sell_price_high"),
        "reassess_ceiling_exit": result.get("updated_ceiling_exit"),
        "reassess_updated_stop_loss": new_stop,
        "reassess_exit_trigger": result.get("exit_trigger"),
        "reassess_timestamp": timestamp_str,
        "reassess_reasoning": reassess_reasoning,
    }
    if new_stop is not None:
        update_kwargs["stop_loss"] = new_stop

    # 9. Update DB
    success = update_decision_point(
        decision_id,
        decision.get("recommendation", "BUY"),
        decision.get("reasoning", ""),
        decision.get("status", "Owned"),
        **{k: v for k, v in update_kwargs.items() if v is not None}
    )
    if success:
        sell_low = result.get("updated_sell_price_low")
        sell_high = result.get("updated_sell_price_high")
        ceiling = result.get("updated_ceiling_exit")
        exit_trig = result.get("exit_trigger") or "-"
        print(f"  [{symbol}] Reassessed: {result.get('sell_action')} | Thesis: {result.get('thesis_status')}")
        print(f"  [{symbol}] Sell Zone: {_fmt_price(sell_low)} - {_fmt_price(sell_high)} | Ceiling: {_fmt_price(ceiling)}")
        print(f"  [{symbol}] Exit Trigger: {exit_trig[:80]}{'...' if len(str(exit_trig)) > 80 else ''}")
    else:
        print(f"  [{symbol}] DB update failed.")

    return decision_id if success else None


def main():
    # Ensure DB schema is up to date (adds missing columns like sell_price_*, reassess_*)
    init_db()

    parser = argparse.ArgumentParser(description="Reassess owned positions via Sell Council")
    parser.add_argument(
        "tickers",
        nargs="*",
        help="Optional ticker symbols. If omitted, reassess all status='Owned' positions.",
    )
    args = parser.parse_args()

    if args.tickers:
        symbols = [t.upper().strip() for t in args.tickers if t.strip()]
        # Resolve to actual decision records
        decisions = _get_decisions_by_symbols(symbols)
        if not decisions:
            print("No matching decision points found for the given tickers.")
            return 1
        symbols = [d["symbol"] for d in decisions]
    else:
        decisions = _get_owned_positions()
        symbols = [d["symbol"] for d in decisions]
        if not symbols:
            print("No owned positions found. Exiting.")
            return 0

    print(f"Reassessing {len(symbols)} position(s): {', '.join(symbols)}")
    print("=" * 60)

    reassessed_ids = []
    for i, symbol in enumerate(symbols):
        decision_id = _reassess_one(symbol)
        if decision_id:
            reassessed_ids.append(decision_id)
        # Deep Research 60s cooldown between tickers
        if i < len(symbols) - 1:
            print("  Waiting 60s (Deep Research cooldown)...")
            time.sleep(60)

    print("=" * 60)
    print("Reassessment complete.")

    # Print table of reassessed positions
    if reassessed_ids:
        _print_reassessment_table(reassessed_ids)

    return 0


def _print_reassessment_table(decision_ids: List[int]) -> None:
    """Print a table of reassessed decision points."""
    rows = []
    for did in decision_ids:
        d = get_decision_point(did)
        if not d:
            continue
        rows.append({
            "Symbol": d.get("symbol", "-"),
            "Action": d.get("reassess_sell_action", "-"),
            "Thesis": d.get("reassess_thesis_status", "-"),
            "Sell Low": _fmt_price(d.get("reassess_sell_price_low") or d.get("sell_price_low")),
            "Sell High": _fmt_price(d.get("reassess_sell_price_high") or d.get("sell_price_high")),
            "Ceiling": _fmt_price(d.get("reassess_ceiling_exit") or d.get("ceiling_exit")),
            "Stop": _fmt_price(d.get("reassess_updated_stop_loss") or d.get("stop_loss")),
            "Exit Trigger": _truncate(str(d.get("reassess_exit_trigger") or d.get("exit_trigger") or "-"), 40),
            "Reassessed": d.get("reassess_timestamp", "-"),
        })
    if not rows:
        return
    # Build table
    cols = ["Symbol", "Action", "Thesis", "Sell Low", "Sell High", "Ceiling", "Stop", "Exit Trigger", "Reassessed"]
    widths = [8, 8, 10, 10, 10, 10, 10, 42, 20]
    hdr = "  ".join(c.ljust(w) for c, w in zip(cols, widths))
    sep = "-" * len(hdr)
    print(f"\n{'=' * len(hdr)}")
    print("REASSESSMENT RESULTS")
    print(sep)
    print(hdr)
    print(sep)
    for r in rows:
        print("  ".join(str(r.get(c, "-")).ljust(w)[:w] for c, w in zip(cols, widths)))
    print(sep)


if __name__ == "__main__":
    sys.exit(main())
