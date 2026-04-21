import sqlite3
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import os
import matplotlib.pyplot as plt
import seaborn as sns

DB_NAME = os.getenv("DB_PATH", "data/subscribers.db")

def get_limit_decisions():
    """Fetch 2026 decision points that have a limit price set."""
    conn = sqlite3.connect(DB_NAME)
    
    query = """
        SELECT 
            id, symbol, price_at_decision, 
            recommendation, IFNULL(deep_research_verdict, 'None') as deep_research_verdict, 
            timestamp,
            entry_price_high
        FROM decision_points 
        WHERE timestamp >= '2026-01-01 00:00:00'
          AND entry_price_high IS NOT NULL
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df

def analyze_limit_performance(symbol, start_date_str, original_price, entry_price_high):
    """Iterate through the price history to find the exact trigger date and calculate ROIs."""
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d")
        end_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        
        ticker = yf.Ticker(symbol)
        history = ticker.history(start=start_date, end=end_date)
        
        if history.empty:
            return None
            
        # Baseline Market Performance (If we just bought immediately)
        market_max_price = history['High'].max()
        market_current_price = history['Close'].iloc[-1]
        market_max_roi = ((market_max_price - original_price) / original_price) * 100
        market_current_roi = ((market_current_price - original_price) / original_price) * 100
        
        # Limit Order Logic
        is_triggered = False
        trigger_date = None
        limit_max_roi = None
        limit_current_roi = None
        days_to_trigger = None
        
        # Identify the trigger date sequentially
        for date, row in history.iterrows():
            if row['Low'] <= entry_price_high:
                is_triggered = True
                trigger_date = date
                break
                
        if is_triggered:
            # We filled at entry_price_high on trigger_date
            # The peak must happen AFTER or ON the trigger date
            post_trigger_history = history.loc[trigger_date:]
            limit_max_price = post_trigger_history['High'].max()
            limit_max_roi = ((limit_max_price - entry_price_high) / entry_price_high) * 100
            limit_current_roi = ((market_current_price - entry_price_high) / entry_price_high) * 100
            
            # Days taken to trigger from decision date
            dt_decision = pd.to_datetime(start_date).tz_localize(trigger_date.tzinfo) if trigger_date.tzinfo else pd.to_datetime(start_date)
            days_to_trigger = (trigger_date - dt_decision).days
            
        return {
            'market_max_roi': market_max_roi,
            'market_current_roi': market_current_roi,
            'is_triggered': is_triggered,
            'limit_max_roi': limit_max_roi,
            'limit_current_roi': limit_current_roi,
            'days_to_trigger': days_to_trigger
        }
            
    except Exception as e:
        print(f"Error fetching data for {symbol}: {e}")
        return None

def run_buy_limit_analysis():
    print("Fetching 2026 limit decisions from database...")
    df = get_limit_decisions()
    
    print(f"Analyzing {len(df)} recommendations with limit orders...")
    
    results = []
    
    total = len(df)
    for i, row in df.iterrows():
        if i % 10 == 0:
            print(f"Processing {i}/{total} ({row['symbol']})...")
            
        perf = analyze_limit_performance(
            row['symbol'], 
            row['timestamp'], 
            row['price_at_decision'],
            row['entry_price_high']
        )
        
        if perf:
            results.append({
                'symbol': row['symbol'],
                'recommendation': row['recommendation'],
                'deep_research_verdict': row['deep_research_verdict'],
                'market_max_roi': perf['market_max_roi'],
                'market_current_roi': perf['market_current_roi'],
                'is_triggered': perf['is_triggered'],
                'limit_max_roi': perf['limit_max_roi'],
                'limit_current_roi': perf['limit_current_roi'],
                'days_to_trigger': perf['days_to_trigger']
            })
            
    perf_df = pd.DataFrame(results)
    
    if perf_df.empty:
        print("No limit order performance data could be fetched.")
        return
        
    print("\nCalculations complete. Aggregating Limits Results...")
    
    total_trades = len(perf_df)
    triggered_df = perf_df[perf_df['is_triggered']]
    missed_df = perf_df[~perf_df['is_triggered']]
    
    trigger_rate = (len(triggered_df) / total_trades) * 100 if total_trades > 0 else 0
    
    print("\n===============================")
    print("   BUY LIMIT ORDER DEEP DIVE ")
    print("===============================\n")
    print(f"Total Recommendations with Limit Order: {total_trades}")
    print(f"Total Triggered / Filled:               {len(triggered_df)} ({trigger_rate:.1f}%)")
    print(f"Total Missed:                           {len(missed_df)} ({100 - trigger_rate:.1f}%)\n")
    
    # 1. Triggered Analysis
    if not triggered_df.empty:
        print("--- FOR THE ONES THAT TRIGGERED (FILLED) ---")
        avg_days = triggered_df['days_to_trigger'].mean()
        avg_limit_max_roi = triggered_df['limit_max_roi'].mean()
        avg_market_max_roi = triggered_df['market_max_roi'].mean() # Market hypothetical
        
        limit_win_rate = (triggered_df['limit_max_roi'] >= 10.0).sum() / len(triggered_df) * 100
        market_win_rate = (triggered_df['market_max_roi'] >= 10.0).sum() / len(triggered_df) * 100
        
        print(f"Average time to fill:          {avg_days:.1f} days")
        print(f"Average Peak ROI (Limit Fill): {avg_limit_max_roi:.2f}% (Limit Strategy)")
        print(f"Average Peak ROI (Market Buy): {avg_market_max_roi:.2f}% (Hypothetical Mkt Buy)")
        print(f"Win Rate (>10%) using Limit:   {limit_win_rate:.1f}%")
        print(f"Win Rate (>10%) using Mkt Buy: {market_win_rate:.1f}%\n")
        
    # 2. Missed Analysis
    if not missed_df.empty:
        print("--- FOR THE ONES THAT MISSED (OPPORTUNITY COST) ---")
        avg_missed_max_roi = missed_df['market_max_roi'].mean()
        missed_win_rate = (missed_df['market_max_roi'] >= 10.0).sum() / len(missed_df) * 100
        
        print(f"We missed filling on {len(missed_df)} trades.")
        print(f"Average Peak ROI left on the table: {avg_missed_max_roi:.2f}%")
        print(f"Percentage of missed trades that went up >10%: {missed_win_rate:.1f}%\n")
        
    # Generate Visualizations
    import seaborn as sns
    sns.set_theme(style="whitegrid")
    
    # 1. Pie Chart: Trigger Rate
    plt.figure(figsize=(8, 8))
    labels = ['Triggered/Filled', 'Missed Opportunity (Ran away)']
    sizes = [len(triggered_df), len(missed_df)]
    colors = ['#4CAF50', '#F44336']
    plt.pie(sizes, labels=labels, colors=colors, autopct='%1.1f%%', startangle=90)
    plt.title('BUY LIMIT Execution Rate (2026)')
    plt.savefig('reports/buy_limit_trigger_rate.png')
    plt.close()
    
    # 2. Bar Chart: Peak ROI comparison
    plt.figure(figsize=(10, 6))
    roi_labels = ['Filled via Limit', 'Hypothetical Mkt Buy', 'Missed Opportunities']
    roi_values = [triggered_df['limit_max_roi'].mean(), triggered_df['market_max_roi'].mean(), missed_df['market_max_roi'].mean()]
    sns.barplot(x=roi_labels, y=roi_values, palette=['#4CAF50', '#2196F3', '#F44336'])
    plt.title('Average Peak ROI by Execution Outcome')
    plt.ylabel('Average Peak ROI (%)')
    for i, v in enumerate(roi_values):
        plt.text(i, v + 0.5, f"{v:.2f}%", ha='center', fontweight='bold')
    plt.savefig('reports/buy_limit_roi_comparison.png')
    plt.close()
    
    # 3. Bar Chart: Win Rate comparison
    plt.figure(figsize=(10, 6))
    limit_win = (triggered_df['limit_max_roi'] >= 10.0).sum() / len(triggered_df) * 100
    mkt_win = (triggered_df['market_max_roi'] >= 10.0).sum() / len(triggered_df) * 100
    miss_win = (missed_df['market_max_roi'] >= 10.0).sum() / len(missed_df) * 100
    win_values = [limit_win, mkt_win, miss_win]
    sns.barplot(x=roi_labels, y=win_values, palette=['#4CAF50', '#2196F3', '#F44336'])
    plt.title('Win Rate (>10% Peak) by Execution Outcome')
    plt.ylabel('Win Rate (%)')
    for i, v in enumerate(win_values):
        plt.text(i, v + 1, f"{v:.1f}%", ha='center', fontweight='bold')
    plt.savefig('reports/buy_limit_win_rate_comparison.png')
    plt.close()
    
    print("Plots generated successfully in /reports")

    # Write full detailed report
    report_path = "reports/buy_limit_deep_dive.md"
    os.makedirs("reports", exist_ok=True)
    
    with open(report_path, "w") as f:
        f.write("# BUY LIMIT Recommendation Deep Dive (2026)\n\n")
        f.write(f"- **Total Recommendations with Limit Order:** {total_trades}\n")
        f.write(f"- **Total Triggered:** {len(triggered_df)} ({trigger_rate:.1f}%)\n")
        f.write(f"- **Total Missed:** {len(missed_df)} ({100 - trigger_rate:.1f}%)\n\n")
        
        if not triggered_df.empty:
            f.write("## 1. When Limits Hit (The Fills)\n")
            f.write("For trades where the price dropped enough to hit our limit:\n")
            f.write(f"- **Average Time to Trigger:** {triggered_df['days_to_trigger'].mean():.1f} days\n")
            f.write(f"- **Average Peak ROI (using Limit Price):** {triggered_df['limit_max_roi'].mean():.2f}%\n")
            f.write(f"- **Average Peak ROI (if we had Market Bought):** {triggered_df['market_max_roi'].mean():.2f}%\n")
            f.write(f"- **Win Rate (>10% peak) using Limit:** {(triggered_df['limit_max_roi'] >= 10).sum() / len(triggered_df) * 100:.1f}%\n")
            f.write(f"- **Win Rate (>10% peak) using Mkt:** {(triggered_df['market_max_roi'] >= 10).sum() / len(triggered_df) * 100:.1f}%\n\n")
            
        if not missed_df.empty:
            f.write("## 2. When Limits Miss (The Opportunity Cost)\n")
            f.write("For trades where the price never dropped down to our limit buy point. This is the opportunity cost.\n")
            f.write(f"- **Average Market Peak ROI left on the table:** {missed_df['market_max_roi'].mean():.2f}%\n")
            f.write(f"- **Percentage of missed limit orders that eventually went up >10%:** {(missed_df['market_max_roi'] >= 10).sum() / len(missed_df) * 100:.1f}%\n")

    print(f"Detailed Markdown report saved to {report_path}")

if __name__ == "__main__":
    run_buy_limit_analysis()
