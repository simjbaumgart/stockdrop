
import sys
import os
import requests
from bs4 import BeautifulSoup

# Add the parent directory to sys.path to allow importing app modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

def main():
    # Example URL from previous run (NVDA 10-Q)
    # URL: https://www.sec.gov/Archives/edgar/data/1045810/000104581025000230/nvda-20251026.htm
    url = "https://www.sec.gov/Archives/edgar/data/1045810/000104581025000230/nvda-20251026.htm"
    # url = "https://www.sec.gov/Archives/edgar/data/1045810/000104581025000021/nvda-20250226.htm" # 8-K
    
    print(f"Fetching SEC filing from: {url}")
    
    # SEC requires a user-agent
    headers = {
        "User-Agent": "StockDropResearch bot@stockdrop.com",
        "Accept-Encoding": "gzip, deflate",
        "Host": "www.sec.gov"
    }
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Remove script and style elements
        for script in soup(["script", "style"]):
            script.decompose()
            
        # Get text
        text = soup.get_text()
        
        # Break into lines and remove leading/trailing space on each
        lines = (line.strip() for line in text.splitlines())
        # Break multi-headlines into a line each
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        # Drop blank lines
        text = '\n'.join(chunk for chunk in chunks if chunk)
        
        print(f"\n--- Extracted Text ({len(text)} chars) ---\n")
        print(text[:2000] + "\n\n... [truncated] ...")
        
        # Try to find specific keywords like "Management's Discussion"
        print("\n--- Searching for 'Management's Discussion' ---")
        idx = text.find("Management’s Discussion")
        if idx == -1:
             idx = text.find("Management's Discussion")
             
        if idx != -1:
            print("Found 'Management's Discussion' section!")
            # Extract a substantial chunk (e.g., 5000 characters) to show the user
            excerpt = text[idx:idx+5000]
            print(excerpt[:500] + "\n...[truncated]...")
            
            # Save to file for user review
            output_path = os.path.join(os.path.dirname(__file__), "sec_filing_sample.txt")
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(excerpt)
            print(f"\nSaved 5000 characters of 'Management's Discussion' to: {output_path}")
        else:
            print("Could not find 'Management's Discussion' section marker.")
            
    except Exception as e:
        print(f"Error fetching/parsing SEC filing: {e}")


if __name__ == "__main__":
    main()
