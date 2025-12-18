import requests
import os
import json
import time
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
INTERACTION_ID = "v1_ChdBMmM4YWZLSkhMNmd4TjhQeV9QNjhBMBIXQTJjOGFmS0pITDZneE44UHlfUDY4QTA"
BASE_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"

headers = {
    "Content-Type": "application/json",
    "x-goog-api-key": API_KEY
}

url = f"{BASE_URL}/{INTERACTION_ID}"
resp = requests.get(url, headers=headers)

if resp.status_code == 200:
    data = resp.json()
    if 'outputs' in data and data['outputs']:
        output = data['outputs'][-1]
        text = output.get('text', str(output))
        with open("alab_research_result.md", "w") as f:
            f.write(text)
        print("Success: Wrote to alab_research_result.md")
    else:
        print("No outputs found")
else:
    print(f"Error: {resp.status_code} - {resp.text}")
