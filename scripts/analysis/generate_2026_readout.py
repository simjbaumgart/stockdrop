import sqlite3
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import os
import seaborn as sns
import matplotlib.pyplot as plt
import pytz

DB_NAME = os.getenv("DB_PATH", "subscribers.db")

def get_2026_decisions():
    """Fetch 2026 decision points from the database."""
    conn = sqlite3.connect(DB_NAME)
    
    query = """
        SELECT 
            id, symbol, price_at_decision, 
            recommendation, IFNULL(deep_research_verdict, 'None') as deep_research_verdict, 
            timestamp,
            entry_price_low, entry_price_high
        FROM decision_points 
        WHERE timestamp LIKE '2026%'
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df

def fetch_historical_performance(symbol, start_date_str, original_price, sp500_history, entry_price_high=None):
    """Fetch yfinance data to calculate max ROI, current ROI, and drawdown for the stock and SP500."""
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d")
        end_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        
        ticker = yf.Ticker(symbol)
        history = ticker.history(start=start_date, end=end_date)
        
        if history.empty:
            return None
            
        # Standard Performance
        max_price = history['High'].max()
        min_price = history['Low'].min()
        current_price = history['Close'].iloc[-1]
        
        # Calculate standard ROI regardless of limits
        max_roi = ((max_price - original_price) / original_price) * 100
        current_roi = ((current_price - original_price) / original_price) * 100
        max_drawdown = ((min_price - original_price) / original_price) * 100
        
        # Calculate SP500 ROI for identical time period
        sp500_max_roi = None
        sp500_current_roi = None
        if not sp500_history.empty:
            # S&P 500 indices are timezone-aware usually, localize our start/end if needed,
            # or simply slice by string datetime index.
            # yfinance returns tz-aware index.
            try:
                # Get the S&P 500 data from the start date onwards
                tz = sp500_history.index.tzinfo
                if tz is None:
                    # if naive index
                    start_dt = pd.to_datetime(start_date)
                else:
                    start_dt = pd.to_datetime(start_date).tz_localize(tz)
                
                sp500_slice = sp500_history.loc[start_dt:]
                
                if not sp500_slice.empty:
                    sp500_start_price = sp500_slice['Close'].iloc[0]
                    sp500_max_price = sp500_slice['High'].max()
                    sp500_current_price = sp500_slice['Close'].iloc[-1]
                    
                    sp500_max_roi = ((sp500_max_price - sp500_start_price) / sp500_start_price) * 100
                    sp500_current_roi = ((sp500_current_price - sp500_start_price) / sp500_start_price) * 100
            except Exception as e:
                print(f"Error calculating SP500 ROI for {symbol}: {e}")
        
        # Evaluate Entry Range Limit Order logic
        is_triggered = False
        limit_max_roi = None
        limit_current_roi = None
        limit_drawdown = None
        miss_distance = None
        has_limit = False
        
        if pd.notnull(entry_price_high):
            has_limit = True
            # Check if history goes below our high entry point
            if history['Low'].min() <= entry_price_high:
                is_triggered = True
                
                # Assume filled at entry_price_high
                limit_max_roi = ((max_price - entry_price_high) / entry_price_high) * 100
                limit_current_roi = ((current_price - entry_price_high) / entry_price_high) * 100
                limit_drawdown = ((min_price - entry_price_high) / entry_price_high) * 100
            else:
                # Missed the entry
                miss_distance = ((history['Low'].min() - entry_price_high) / entry_price_high) * 100
        
        return {
            'max_roi': max_roi,
            'current_roi': current_roi,
            'max_drawdown': max_drawdown,
            'current_price': current_price,
            'is_triggered': is_triggered,
            'limit_max_roi': limit_max_roi,
            'limit_current_roi': limit_current_roi,
            'limit_drawdown': limit_drawdown,
            'miss_distance': miss_distance,
            'has_limit': has_limit,
            'sp500_max_roi': sp500_max_roi,
            'sp500_current_roi': sp500_current_roi
        }
            
    except Exception as e:
        print(f"Error fetching data for {symbol}: {e}")
        return None

def generate_readout():
    print("Fetching 2026 decision data from database...")
    df = get_2026_decisions()
    
    # Exclude basic AVOID since they aren't meant to be traded anyway
    df = df[df['recommendation'] != 'AVOID']
    print(f"Analyzing {len(df)} recommendations (excluding basic AVOIDs)...")
    
    # Pre-fetch S&P 500 history for 2026 onwards to avoid fetching it for every symbol
    print("Fetching S&P 500 baseline data...")
    sp500_history = yf.Ticker("^GSPC").history(start="2026-01-01")
    
    results = []
    
    total = len(df)
    for i, row in df.iterrows():
        if i % 10 == 0:
            print(f"Processing {i}/{total} ({row['symbol']})...")
            
        perf = fetch_historical_performance(
            row['symbol'], 
            row['timestamp'], 
            row['price_at_decision'],
            sp500_history,
            row['entry_price_high']
        )
        
        if perf:
            results.append({
                'symbol': row['symbol'],
                'recommendation': row['recommendation'],
                'deep_research_verdict': row['deep_research_verdict'],
                'price_at_decision': row['price_at_decision'],
                'max_roi': perf['max_roi'],
                'current_roi': perf['current_roi'],
                'max_drawdown': perf['max_drawdown'],
                'is_triggered': perf['is_triggered'],
                'limit_max_roi': perf['limit_max_roi'],
                'limit_current_roi': perf['limit_current_roi'],
                'miss_distance': perf['miss_distance'],
                'has_limit': perf['has_limit'],
                'sp500_max_roi': perf['sp500_max_roi'],
                'sp500_current_roi': perf['sp500_current_roi']
            })
            
    perf_df = pd.DataFrame(results)
    
    if perf_df.empty:
        print("No performance data could be fetched.")
        return
        
    print("Calculating aggregates and generating plots...")
    
    # Define "Win" as reaching at least +10% Max ROI
    perf_df['is_win'] = perf_df['max_roi'] >= 10.0
    
    # NEW: Deeper Limit Order Metrics
    limit_df = perf_df[perf_df['has_limit']].copy()
    
    print("\n--- Deep Dive: Limit Orders ---")
    print(f"Total non-AVOID trades:                 {len(perf_df)}")
    print(f"Trades with an established Entry Limit: {len(limit_df)}")
    
    overall_trigger_rate = 0
    if len(limit_df) > 0:
        overall_trigger_rate = (limit_df['is_triggered'].sum() / len(limit_df)) * 100
        print(f"True Limit Trigger Rate:                {overall_trigger_rate:.1f}%")
        
        missed_df = limit_df[~limit_df['is_triggered']]
        if len(missed_df) > 0:
            print(f"For the {len(missed_df)} missed limit orders:")
            print(f"  Avg distance missed by:    {missed_df['miss_distance'].mean():.2f}%")
            print(f"  Median distance missed by: {missed_df['miss_distance'].median():.2f}%")
            
        triggered_df = limit_df[limit_df['is_triggered']]
        if len(triggered_df) > 0:
            trig_win_rate = (triggered_df['limit_max_roi'] >= 10.0).sum() / len(triggered_df) * 100
            base_win_rate = (triggered_df['max_roi'] >= 10.0).sum() / len(triggered_df) * 100
            print(f"For the {len(triggered_df)} triggered limit orders:")
            print(f"  Standard Buy Win Rate:     {base_win_rate:.1f}%")
            print(f"  Limit Fill Win Rate:       {trig_win_rate:.1f}%")
            print(f"  (Boost in Win Rate:        {trig_win_rate - base_win_rate:+.1f}%)")
    print("-------------------------------\n")
    
    grouped = perf_df.groupby(['recommendation', 'deep_research_verdict']).agg(
        count=('symbol', 'count'),
        avg_max_roi=('max_roi', 'mean'),
        avg_current_roi=('current_roi', 'mean'),
        win_rate=('is_win', lambda x: (x.sum() / len(x)) * 100),
        has_limit_count=('has_limit', 'sum'),
        trigger_count=('is_triggered', 'sum'),
        limit_avg_max_roi=('limit_max_roi', lambda x: x.mean(skipna=True)),
        avg_sp500_max_roi=('sp500_max_roi', lambda x: x.mean(skipna=True))
    ).reset_index()
    
    # Calculate true trigger rate per group (triggers / has_limit)
    grouped['trigger_rate'] = grouped.apply(
        lambda row: (row['trigger_count'] / row['has_limit_count'] * 100) if row['has_limit_count'] > 0 else 0, 
        axis=1
    )
    
    grouped = grouped[grouped['count'] >= 3]
    grouped = grouped.sort_values(by='avg_max_roi', ascending=False)
    
    # 2. Generate Plot
    plt.figure(figsize=(14, 8))
    # Combine strings for plotting
    perf_df['Cohort'] = perf_df['recommendation'] + " + " + perf_df['deep_research_verdict']
    
    # Filter for plot to match table
    plot_df = perf_df[perf_df.groupby('Cohort')['Cohort'].transform('size') >= 3]
    
    sns.violinplot(
        data=plot_df, 
        x='max_roi', 
        y='Cohort', 
        cut=0, 
        inner='quartile', 
        order=plot_df.groupby('Cohort')['max_roi'].mean().sort_values(ascending=False).index
    )
    plt.axvline(x=10, color='r', linestyle='--', label='10% Target Win')
    plt.title("Distribution of Max ROI by Recommendation Cohort (2026)")
    plt.xlabel("Maximum ROI (%)")
    plt.ylabel("Recommendation + Deep Research Verdict")
    plt.legend()
    plt.tight_layout()
    
    plot_path = "reports/2026_roi_distribution.png"
    os.makedirs("reports", exist_ok=True)
    plt.savefig(plot_path)
    plt.close()
    print(f"Plot saved to {plot_path}")
    
    # 2b. Generate Plot: Win Rate by Cohort
    plt.figure(figsize=(14, 8))
    grouped['Cohort'] = grouped['recommendation'] + " + " + grouped['deep_research_verdict']
    win_rate_df = grouped.sort_values(by='win_rate', ascending=False)
    
    sns.barplot(data=win_rate_df, x='win_rate', y='Cohort')
    
    plt.title("Win Rate (>10% Max ROI) by Recommendation Cohort (2026)")
    plt.xlabel("Win Rate (%)")
    plt.ylabel("Recommendation + Deep Research Verdict")
    plt.tight_layout()
    
    win_rate_plot_path = "reports/2026_win_rate_distribution.png"
    plt.savefig(win_rate_plot_path)
    plt.close()
    print(f"Plot saved to {win_rate_plot_path}")
    
    # 2c. Generate Plot: ROI vs SP500 ROI by Cohort
    plt.figure(figsize=(14, 8))
    # Melt data for grouped bar chart
    grouped_melt = grouped.melt(id_vars='Cohort', value_vars=['avg_max_roi', 'avg_sp500_max_roi'], var_name='Metric', value_name='ROI')
    
    # Map back to more readable labels
    grouped_melt['Metric'] = grouped_melt['Metric'].replace({'avg_max_roi': 'Stock Avg Max ROI', 'avg_sp500_max_roi': 'SP500 Avg Max ROI'})
    
    sns.barplot(data=grouped_melt, x='ROI', y='Cohort', hue='Metric')
    
    plt.title("Average Max ROI vs SP500 Average Max ROI by Cohort (2026)")
    plt.xlabel("Average ROI (%)")
    plt.ylabel("Recommendation + Deep Research Verdict")
    plt.legend(title='Metric')
    plt.tight_layout()
    
    roi_comparison_plot_path = "reports/2026_roi_comparison.png"
    plt.savefig(roi_comparison_plot_path)
    plt.close()
    print(f"Plot saved to {roi_comparison_plot_path}")
    
    # 3. Generate Markdown Output
    report_content = [
        "# 2026 Trading Performance Readout\n",
        "This report analyzes the performance of all system recommendations (excluding generic AVOID) made throughout 2026.\n",
        "**Metrics Defined:**",
        "- **Standard Avg Max ROI (%)**: The average of the highest return achieved by stocks in this group based on the price at decision.",
        "- **Win Rate (%)**: The percentage of stocks in this group that achieved a Max ROI of 10% or greater.",
        "- **Entry Trigger Rate (%)**: How often the lowest price fell below the recommended `entry_price_high` limit order.",
        "- **Limit Avg Max ROI (%)**: For orders that triggered, what was the average maximum ROI from the fill price.",
        "- **SP500 Avg Max ROI (%)**: The average maximum S&P 500 performance measured over the identical timeframe as each stock in this group.",
        "- *Note: Groups with fewer than 3 trades are excluded for statistical significance.*\n",
        "## Performance Table\n",
        "| Recommendation | DR Verdict | N | Std Max ROI | SP500 Max ROI | Trigger Rate | Limit Fill Max ROI | Win Rate (>10%) |",
        "|---|---|---|---|---|---|---|---|"
    ]
    
    for _, row in grouped.iterrows():
        limit_roi_str = f"{row['limit_avg_max_roi']:.2f}%" if pd.notnull(row['limit_avg_max_roi']) else "N/A"
        sp500_roi_str = f"{row['avg_sp500_max_roi']:.2f}%" if pd.notnull(row['avg_sp500_max_roi']) else "N/A"
        report_content.append(
            f"| {row['recommendation']} | {row['deep_research_verdict']} | {row['count']} | "
            f"{row['avg_max_roi']:.2f}% | {sp500_roi_str} | {row['trigger_rate']:.1f}% | "
            f"{limit_roi_str} | {row['win_rate']:.1f}% |"
        )
        
    report_content.append(f"\n## Visual Analysis")
    report_content.append(f"\n### Distribution of Max ROI")
    report_content.append(f"![ROI Distribution](2026_roi_distribution.png)\n")
    
    report_content.append(f"\n### Win Rate by Cohort")
    report_content.append(f"![Win Rate Distribution](2026_win_rate_distribution.png)\n")
    
    report_content.append(f"\n### Stock ROI vs S&P 500 Baseline")
    report_content.append(f"![ROI Comparison](2026_roi_comparison.png)\n")
    
    report_content.append("\n## Overall Actionable Stats\n")
    report_content.append(f"- **Total Trades Analyzed (No AVOID):** {len(perf_df)}")
    report_content.append(f"- **Overall System Win Rate (>10% Peak):** {(perf_df['is_win'].sum() / len(perf_df)) * 100:.1f}%")
    report_content.append(f"- **Trades with Established Entry Limit:** {len(limit_df)}")
    report_content.append(f"- **True Limit Order Trigger Rate:** {overall_trigger_rate:.1f}%")
    
    if len(limit_df) > 0 and len(missed_df) > 0:
        report_content.append(f"\n### Deep Dive: Limit Order Triggers")
        report_content.append(f"Out of the {len(limit_df)} trades that actually *had* a limit assigned, only {len(triggered_df)} triggered ({overall_trigger_rate:.1f}%).")
        report_content.append(f"For the **{len(missed_df)} missing orders**, the price on average dropped to within **{missed_df['miss_distance'].mean():.2f}%** of the target limit (Median: {missed_df['miss_distance'].median():.2f}%).")
        
        trig_win_rate = (triggered_df['limit_max_roi'] >= 10.0).sum() / len(triggered_df) * 100
        base_win_rate = (triggered_df['max_roi'] >= 10.0).sum() / len(triggered_df) * 100
        report_content.append(f"\nFor the **{len(triggered_df)} triggered limits**, getting the better entry price boosted the win rate from {base_win_rate:.1f}% to **{trig_win_rate:.1f}%** (a {trig_win_rate - base_win_rate:+.1f}% improvement).")
    
    report_path = "reports/2026_trading_readout.md"
    
    with open(report_path, "w") as f:
        f.write("\n".join(report_content))
        
    print(f"\nReport successfully generated at: {report_path}")

if __name__ == "__main__":
    generate_readout()
