import requests
import json
import datetime
import os
from dateutil import parser

# Constants
API_KEY = "5477117586msh5d353b0362a0a36p119d3fjsn29a2acf5e2d8"
HOST = "seeking-alpha.p.rapidapi.com"
SYMBOL = "GOOG"
REPORT_DIR = "experiment_data"
REPORT_FILE = os.path.join(REPORT_DIR, "google_analysis_last_week.html")

headers = {
    "x-rapidapi-key": API_KEY,
    "x-rapidapi-host": HOST
}

def call_endpoint(endpoint, params=None):
    url = f"https://{HOST}/{endpoint}"
    print(f"Calling {url} with params {params}...")
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error calling {endpoint}: {e}")
        return None

def get_analysis_list(symbol, since_date):
    """
    Fetches analysis articles for the symbol.
    Returns a list of articles published after since_date.
    """
    all_articles = []
    page_number = 1
    page_size = 20 # Maximize to reduce calls
    
    while True:
        # Note: The API documentation/experiment used 'id' for symbol in analysis/v2/list
        params = {"id": symbol, "size": page_size, "number": page_number}
        data = call_endpoint("analysis/v2/list", params)
        
        if not data or 'data' not in data:
            break
            
        articles = data['data']
        if not articles:
            break
            
        # Check dates
        older_articles_found = False
        for article in articles:
            attrs = article.get('attributes', {})
            publish_on_str = attrs.get('publishOn')
            if not publish_on_str:
                continue
                
            try:
                publish_date = parser.parse(publish_on_str)
                # Ensure timezone awareness compatibility if needed, but simple comparison usually works if both aware
                if publish_date.tzinfo is None:
                    publish_date = publish_date.replace(tzinfo=datetime.timezone.utc)
                
                if publish_date >= since_date:
                    all_articles.append(article)
                else:
                    older_articles_found = True
            except Exception as e:
                print(f"Error parsing date {publish_on_str}: {e}")
        
        if older_articles_found:
            break # We found articles older than our threshold, so we can stop fetching pages
        
        page_number += 1
        
    return all_articles

def fetch_article_details(article_id):
    details = call_endpoint("analysis/v2/get-details", {"id": article_id})
    if details and 'data' in details:
        return details['data']
    return None

def generate_html_report(articles):
    # Sort articles by date (newest first)
    articles.sort(key=lambda x: parser.parse(x['attributes']['publishOn']), reverse=True)
    
    html_content = """
    <html>
    <head>
        <title>Google Analysis - Last 7 Days</title>
        <style>
            body { font-family: sans-serif; line-height: 1.6; max-width: 800px; margin: 0 auto; padding: 20px; }
            h1 { color: #333; }
            h2 { color: #555; border-bottom: 1px solid #eee; padding-bottom: 10px; margin-top: 40px; }
            .meta { color: #777; font-size: 0.9em; margin-bottom: 20px; }
            .toc { background: #f9f9f9; padding: 20px; border-radius: 5px; margin-bottom: 30px; }
            .toc ul { list-style: none; padding: 0; }
            .toc li { margin-bottom: 5px; }
            .article { margin-bottom: 60px; border: 1px solid #ddd; padding: 20px; border-radius: 8px; }
            img { max-width: 100%; height: auto; }
        </style>
    </head>
    <body>
        <h1>Google Analysis Report</h1>
        <p>Generated on: """ + datetime.datetime.now().strftime("%Y-%m-%d %H:%M") + """</p>
        
        <div class="toc">
            <h3>Table of Contents</h3>
            <ul>
    """
    
    # Generate TOC
    full_body_content = ""
    
    for article in articles:
        article_id = article['id']
        attrs = article.get('attributes', {})
        title = attrs.get('title', 'No Title')
        
        # Add to TOC
        html_content += f'<li><a href="#article-{article_id}">{title}</a></li>'
        
        # Fetch details
        print(f"Fetching full content for: {title}")
        details = fetch_article_details(article_id)
        
        content = "Content not available"
        if details:
             d_attrs = details.get('attributes', {})
             content = d_attrs.get('content', '')
             if d_attrs.get('isLocked'):
                 content = "<p><strong>Note: This article is locked/premium content. Preview only.</strong></p>" + content
        
        publish_date = parser.parse(attrs.get('publishOn')).strftime("%Y-%m-%d %H:%M")
        author = attrs.get('author', {}).get('name', 'Unknown Author') # Structure might vary, check previous output if needed
        # Actually author seems not to be in 'attributes' of list item directly usually, but let's check
        # For now, let's trust 'publishOn' and 'title'.
        
        full_body_content += f"""
        <div id="article-{article_id}" class="article">
            <h2>{title}</h2>
            <div class="meta">Published: {publish_date}</div>
            <div class="content">
                {content}
            </div>
            <hr>
        </div>
        """
        
    html_content += """
            </ul>
        </div>
    """
    
    html_content += full_body_content
    html_content += "</body></html>"
    
    return html_content

def main():
    print("--- Fetching Google Analysis (Last 7 Days) ---")
    
    # Calculate threshold: 7 days ago
    # Using timezone aware UTC for comparison
    threshold = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)
    print(f"Date Threshold: {threshold}")
    
    # 1. Get List
    print("Fetching article list...")
    articles = get_analysis_list(SYMBOL, threshold)
    print(f"Found {len(articles)} articles published since {threshold.date()}")
    
    if not articles:
        print("No articles found.")
        return

    # 2. Generate Report
    print("Generating HTML report...")
    report_html = generate_html_report(articles)
    
    # 3. Save
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report_html)
    
    print(f"Report saved to: {REPORT_FILE}")

if __name__ == "__main__":
    main()
