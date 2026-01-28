import logging
import os
from dotenv import load_dotenv

# Load .env explicitly for the script
load_dotenv()

from app.services.seeking_alpha_service import seeking_alpha_service

# Setup
logging.basicConfig(level=logging.INFO)
ticker = "VIPS"

print(f"--- Testing Dynamic Fetch for {ticker} ---")

# 1. Clean previous data to ensure fresh fetch
import json
import os
path = "experiment_data/agent_context.json"
if os.path.exists(path):
    with open(path, "r") as f:
        data = json.load(f)
    if "stocks" in data and ticker in data["stocks"]:
        print(f"Removing existing {ticker} data for clean test...")
        del data["stocks"][ticker]
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

# 2. Trigger Fetch via get_counts (simulating Console Log step)
print("\nCalling get_counts()...")
counts = seeking_alpha_service.get_counts(ticker)
print(f"Counts: {counts}")

if counts['total'] > 0:
    print("SUCCESS: Data fetched and counted.")
else:
    print("FAILURE: No data found after fetch.")

# 3. Verify get_evidence works
print("\nCalling get_evidence()...")
evidence = seeking_alpha_service.get_evidence(ticker)
print(f"Evidence Length: {len(evidence)} chars")

if "Seeking Alpha Data: Not Available" not in evidence:
     print("SUCCESS: Evidence generated.")
else:
     print("FAILURE: Evidence generation failed.")

# 4. Verify WSB Fetching
print("\n--- Testing WSB Dynamic Fetch ---")
# Force clear WSB for test
path = "experiment_data/agent_context.json"
if os.path.exists(path):
    with open(path, "r") as f:
        data = json.load(f)
    if "wall_street_breakfast" in data:
        print("Clearing existing WSB data...")
        data["wall_street_breakfast"] = []
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

print("Calling fetch_wall_street_breakfast()...")
wsb_items = seeking_alpha_service.fetch_wall_street_breakfast()
print(f"Fetched {len(wsb_items)} WSB items.")

if len(wsb_items) > 0:
    print("SUCCESS: WSB Data fetched.")
    print(f"Title: {wsb_items[0].get('title')}")
else:
    print("FAILURE: No WSB data fetched.")
