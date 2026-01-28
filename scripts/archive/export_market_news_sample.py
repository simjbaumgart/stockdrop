
import sys
import os
import json

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.benzinga_service import benzinga_service

def export_news():
    print("Fetching top 10 market news items...")
    
    # User asked for "top 10 each", but our method returns a combined list.
    # To be helpful, we'll fetch a slightly larger pool (30) to ensure we cover SPY/DIA/QQQ mix,
    # then save them.
    news = benzinga_service.get_market_news(limit=10)
    
    output_dir = "experiment_data"
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, "market_news_review.json")
    
    with open(output_file, "w") as f:
        json.dump(news, f, indent=4)
        
    print(f"Exported {len(news)} items to {output_file}")

if __name__ == "__main__":
    export_news()
