import sys
import os
import pandas as pd
from datetime import datetime

# Add project root to path (one level up from scripts)
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
sys.path.append(project_root)

from app.services.stock_service import stock_service

def show_cached_table():
    print("--- Fetching Large Cap Movers with Cached Indicators ---")
    
    # We use stock_service to get the movers, which uses tradingview_service.get_top_movers
    # This will trigger the global scan with the new columns
    movers = stock_service.get_large_cap_movers()
    
    if not movers:
        print("No movers found.")
        return

    print(f"\nFound {len(movers)} movers.")
    print("\n--- Sample Data (First 5 Entries) ---")
    
    # Convert to DataFrame for broader view if possible, or just print formatted
    data_for_display = []
    
    for mover in movers[:5]:
        cached = mover.get('cached_indicators', {})
        row = {
            "Symbol": mover['symbol'],
            "Region": mover.get('region'),
            "Screener": mover.get('screener'),
            "Exchange": mover.get('exchange'),
            
            # Key Data
            "Price": mover['price'],
            "Change %": f"{mover['change_percent']:.2f}%",
            "OHLC": f"O:{cached.get('open'):.1f} H:{cached.get('high'):.1f} L:{cached.get('low'):.1f}", 
            
            # Performance
            "Perf W": f"{cached.get('perf_w'):.2f}%" if cached.get('perf_w') is not None else "-",
            "Perf YTD": f"{cached.get('perf_ytd'):.2f}%" if cached.get('perf_ytd') is not None else "-",
            
            # Technicals & Oscillators
            "Rating": f"{cached.get('recommend_all'):.2f}",
            "RSI": cached.get('rsi'),
            "Stoch K": f"{cached.get('stoch_k'):.2f}" if cached.get('stoch_k') else "-",
            "Mom": f"{cached.get('mom'):.2f}" if cached.get('mom') else "-",
            
            # Trend
            "SMA50": f"{cached.get('sma50'):.1f}" if cached.get('sma50') else "-",
            "SMA200": f"{cached.get('sma200'):.1f}" if cached.get('sma200') else "-",
            "MACD H": f"{cached.get('macd_hist'):.2f}" if cached.get('macd_hist') else "-",

            # Volatility & Context
            "ATR": f"{cached.get('atr'):.2f}" if cached.get('atr') else "-",
            "Beta": f"{cached.get('beta'):.2f}" if cached.get('beta') else "-",
            "RVOL": f"{cached.get('rvol'):.1f}x" if cached.get('rvol') else "-",
            "52W H/L": f"{cached.get('high52'):.1f}/{cached.get('low52'):.1f}" if cached.get('high52') else "-",

            # Financials - Valuation
            "P/E": f"{cached.get('pe_ratio'):.2f}" if cached.get('pe_ratio') else "-",
            "P/B": f"{cached.get('pb_ratio'):.2f}" if cached.get('pb_ratio') else "-",
            "EV/EBITDA": f"{cached.get('ev_ebitda'):.2f}" if cached.get('ev_ebitda') else "-",
            "Div Yld": f"{cached.get('div_yield'):.2f}%" if cached.get('div_yield') else "-",
            
            # Financials - Income
            "Revenue": f"${cached.get('revenue')/1e9:.1f}B" if cached.get('revenue') else "-",
            "Rev Gr": f"{cached.get('rev_growth'):.1f}%" if cached.get('rev_growth') else "-",
            "Gr Mgn": f"{cached.get('gross_margin'):.1f}%" if cached.get('gross_margin') else "-",
            "Op Mgn": f"{cached.get('op_margin'):.1f}%" if cached.get('op_margin') else "-",
            "Net Inc": f"${cached.get('net_income')/1e9:.1f}B" if cached.get('net_income') else "-",
            "EPS": f"{cached.get('eps'):.2f}" if cached.get('eps') else "-",
            
            # Financials - Balance Sheet
            "Cash": f"${cached.get('cash')/1e9:.1f}B" if cached.get('cash') else "-",
            "Debt": f"${cached.get('total_debt')/1e9:.1f}B" if cached.get('total_debt') else "-",
            "D/E": f"{cached.get('debt_to_equity'):.2f}" if cached.get('debt_to_equity') else "-",
            "Curr R": f"{cached.get('current_ratio'):.2f}" if cached.get('current_ratio') else "-",
            
            # Financials - Cash Flow
            "FCF": f"${cached.get('fcf')/1e9:.1f}B" if cached.get('fcf') else "-"
        }
        data_for_display.append(row)
        
    df = pd.DataFrame(data_for_display)
    # Adjust display options
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 1000)
    
    print(df.to_string(index=False))
    
    print("\n\n--- Raw Dictionary for First Entry ---")
    import json
    print(json.dumps(movers[0], indent=2, default=str))

if __name__ == "__main__":
    show_cached_table()
