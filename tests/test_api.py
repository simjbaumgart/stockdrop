import os
import requests
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
base_url = "https://generativelanguage.googleapis.com/v1beta/interactions"

headers = {
    "Content-Type": "application/json",
    "x-goog-api-key": api_key
}

payload = {
    "input": "Tell me about the history of artificial intelligence.",
    "agent": "deep-research-pro-preview-12-2025", 
    "background": True 
}

print("Sending request to:", base_url)
try:
    response = requests.post(base_url, headers=headers, json=payload, timeout=20)
    print("Status code:", response.status_code)
    print("Response text:", response.text)
except Exception as e:
    print("Error:", e)
