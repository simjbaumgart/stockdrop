import os
import requests
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
interaction_id = "v1_ChZtU3VyYWMtV0ZzZUN2ZElQelpxOElREhZtU3VyYWMtV0ZzZUN2ZElQelpxOElR"
poll_url = f"https://generativelanguage.googleapis.com/v1beta/interactions/{interaction_id}"
headers = {
    "x-goog-api-key": api_key
}

try:
    response = requests.get(poll_url, headers=headers)
    data = response.json()
    print("Keys in response:", data.keys())
    if 'outputs' in data:
        print("Number of outputs:", len(data['outputs']))
    else:
        print("NO OUTPUTS KEY")
        
    if 'response' in data:
        print("Response key exists!")
        print("Response type:", type(data['response']))
except Exception as e:
    print("Error:", e)
