
import sys
import os
import json
import logging
import pandas as pd
from datetime import datetime
from fpdf import FPDF
import numpy as np

# --- Setup local repo path for DefeatBeta ---
repo_path = os.path.join(os.getcwd(), 'defeatbeta-repo')
if os.path.exists(repo_path):
    sys.path.insert(0, repo_path)
    print(f"Added {repo_path} to sys.path")
else:
    print("Warning: defeatbeta-repo not found locally. DefeatBeta features may fail.")

try:
    from defeatbeta_api.data.ticker import Ticker
    # Verify import
    import defeatbeta_api
    print(f"Imported defeatbeta_api from: {os.path.dirname(defeatbeta_api.__file__)}")
except ImportError as e:
    print(f"Error importing defeatbeta_api: {e}")
    sys.exit(1)

# --- Import TradingView Service (Assuming it's in python path) ---
# We need to make sure 'app' is importable. Assuming script is run from root.
sys.path.append(os.getcwd())
try:
    from app.services.tradingview_service import tradingview_service
except ImportError as e:
    print(f"Error importing tradingview_service: {e}")
    sys.exit(1)



DATA_DIR_BASE = "data/DefeatBeta_data"
SYMBOL = "GOOG" 
DATA_DIR = os.path.join(DATA_DIR_BASE, SYMBOL)

def setup_dir():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
        print(f"Created directory: {DATA_DIR}")


def fetch_technicals():
    print("Fetching Technicals...")
    try:
        # Get indicators
        indicators = tradingview_service.get_technical_indicators(SYMBOL, region="US")
        # Get analysis (summary)
        analysis = tradingview_service.get_technical_analysis(SYMBOL, region="US")
        
        data = {
            "indicators": indicators,
            "analysis": analysis.get("summary", {}) if analysis else {},
            "moving_averages": analysis.get("moving_averages", {}) if analysis else {},
            "oscillators": analysis.get("oscillators", {}) if analysis else {}
        }
        
        # Save raw
        with open(os.path.join(DATA_DIR, "technicals.json"), "w") as f:
            json.dump(data, f, indent=2)
            
        return data
    except Exception as e:
        print(f"Error fetching technicals: {e}")
        return {}

def fetch_transcripts(ticker_obj):
    print("Fetching Earnings Transcripts...")
    try:
        transcripts_obj = ticker_obj.earning_call_transcripts()
        df = transcripts_obj.get_transcripts_list()
        
        if df.empty or 'transcripts' not in df.columns:
            print("No transcripts found.")
            return ""

        # Filter for content
        def has_content(x):
            if isinstance(x, list): return len(x) > 0
            if isinstance(x, np.ndarray): return x.size > 0
            return False

        df_content = df[df['transcripts'].apply(has_content)]
        
        if df_content.empty:
            print("No non-empty transcripts found.")
            return ""
            
        # Get latest
        if 'report_date' in df_content.columns:
            df_content['report_date'] = pd.to_datetime(df_content['report_date'])
            df_content = df_content.sort_values('report_date', ascending=False)
            
        latest = df_content.iloc[0]
        report_date = latest.get('report_date', 'Unknown Date')
        content_raw = latest.get('transcripts')
        
        # Normalize list
        if isinstance(content_raw, np.ndarray):
            content_list = content_raw.tolist()
        else:
            content_list = content_raw
            
        full_text = f"Earnings Call Transcript - Date: {report_date}\n\n"
        
        for item in content_list:
            text = item.get('content', '')
            if not text:
                # Fallback extraction
                text = " ".join([str(v) for k,v in item.items() if k not in ['paragraph_number', 'speaker']])
            
            speaker = item.get('speaker', 'Unknown')
            full_text += f"{speaker}: {text}\n\n"
            
        # Save raw
        with open(os.path.join(DATA_DIR, "transcript.txt"), "w") as f:
            f.write(full_text)
            
        return full_text
    except Exception as e:
        print(f"Error fetching transcripts: {e}")
        import traceback
        traceback.print_exc()
        return ""

def fetch_news(ticker_obj):
    print("Fetching News...")
    try:
        news_obj = ticker_obj.news()
        # news_obj.get_news_list() returns the dataframe
        df = news_obj.get_news_list()
        
        if df.empty:
            print("No news found.")
            return []
            
        # Convert to list of dicts
        # Ensure date sorting if possible
        if 'provider_publish_time' in df.columns:
             df = df.sort_values('provider_publish_time', ascending=False)
        elif 'report_date' in df.columns: # Found in data/news.py code
             df = df.sort_values('report_date', ascending=False)
             
        news_items = df.head(20).to_dict('records') # Top 20
        
        # Save raw
        with open(os.path.join(DATA_DIR, "news.json"), "w") as f:
             json.dump(news_items, f, indent=2, default=str)
             
        return news_items
    except Exception as e:
        print(f"Error fetching news: {e}")
        return []

def generate_pdf(tech_data, trans_text, news_items):
    print("Generating PDF...")
    pdf = FPDF()
    pdf.add_page()
    
    # Title
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, f"Deep Dive Report: {SYMBOL}", 0, 1, 'C')
    pdf.ln(5)
    
    # Date
    pdf.set_font("Arial", '', 10)
    pdf.cell(0, 10, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", 0, 1, 'C')
    pdf.ln(10)
    
    # --- Technicals ---
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(0, 10, "Technical Analysis", 0, 1)
    pdf.ln(2)
    
    pdf.set_font("Arial", '', 11)
    
    inds = tech_data.get("indicators", {})
    summary = tech_data.get("analysis", {})
    
    # Summary Table
    pdf.cell(0, 8, f"Price: ${inds.get('close', 0):.2f}", 0, 1)
    pdf.cell(0, 8, f"Recommendation: {summary.get('RECOMMENDATION', 'N/A')} (Buy: {summary.get('BUY', 0)}, Sell: {summary.get('SELL', 0)})", 0, 1)
    pdf.cell(0, 8, f"RSI: {inds.get('rsi', 0):.2f}", 0, 1)
    pdf.cell(0, 8, f"SMA 200: {inds.get('sma200', 0):.2f}", 0, 1)
    pdf.ln(5)
    
    # --- News ---
    pdf.add_page()
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(0, 10, "Recent News", 0, 1)
    pdf.ln(2)
    
    pdf.set_font("Arial", '', 10)
    for item in news_items:
        title = item.get('title', 'No Title')
        pub = item.get('publisher', 'Unknown')
        date = item.get('report_date', 'Unknown')
        link = item.get('link', '')
        
        # Sanitize for latin-1
        title = title.encode('latin-1', 'replace').decode('latin-1')
        pub = pub.encode('latin-1', 'replace').decode('latin-1')
        
        pdf.set_font("Arial", 'B', 10)
        pdf.multi_cell(0, 5, f"{title}")
        pdf.set_font("Arial", '', 9)
        pdf.cell(0, 5, f"{pub} | {date}", 0, 1)
        pdf.set_font("Arial", 'I', 8)
        pdf.cell(0, 5, f"{link}", 0, 1)
        pdf.ln(3)
        
    # --- Transcript ---
    if trans_text:
        pdf.add_page()
        pdf.set_font("Arial", 'B', 14)
        pdf.cell(0, 10, "Latest Earnings Transcript", 0, 1)
        pdf.ln(5)
        
        pdf.set_font("Arial", '', 9)
        # Handle large text
        try:
            safe_text = trans_text.encode('latin-1', 'replace').decode('latin-1')
            pdf.multi_cell(0, 5, safe_text)
        except Exception as e:
            print(f"Error printing transcript: {e}")
            pdf.multi_cell(0, 5, "Error rendering transcript text.")
    else:
        pdf.add_page()
        pdf.set_font("Arial", 'I', 12)
        pdf.cell(0, 10, "No Transcript Available", 0, 1)

    # Save
    out_path = os.path.join(DATA_DIR, "GOOG_Comprehensive_Report.pdf")
    pdf.output(out_path)
    print(f"PDF saved to: {out_path}")
    return out_path

def main():
    setup_dir()
    
    # 1. Technicals
    tech_data = fetch_technicals()
    
    # 2. DefeatBeta Data
    ticker = Ticker(SYMBOL)
    trans_text = fetch_transcripts(ticker)
    news_items = fetch_news(ticker)
    
    # 3. Generate PDF
    generate_pdf(tech_data, trans_text, news_items)
    
    print("\nDone! Check temp_data_GOOG/ for results.")

if __name__ == "__main__":
    main()
