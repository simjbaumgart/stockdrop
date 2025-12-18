import requests
import os
import json
import time
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    print("Error: GEMINI_API_KEY not found.")
    exit(1)

# Base URL for the Interactions API
base_url = "https://generativelanguage.googleapis.com/v1beta/interactions"
headers = {
    "Content-Type": "application/json",
    "x-goog-api-key": API_KEY
}

# Payload - using a simple analyze task
payload = {
    "input": "Analyze the current competitive landscape of EV batteries in Europe for 2025. Focus on supply chain risks.",
    "agent": "deep-research-pro-preview-12-2025",
    "background": True
}

print(f"Making POST request to {base_url}...")
try:
    response = requests.post(base_url, headers=headers, json=payload)
    
    print(f"Status Code: {response.status_code}")
    print(f"Response Headers: {json.dumps(dict(response.headers), indent=2)}")
    
    if response.status_code != 200:
        print(f"Error Response: {response.text}")
        exit(1)
        
    data = response.json()
    print("Initial Response Body:")
    print(json.dumps(data, indent=2))
    
    # Check for the interaction id
    interaction_id = data.get('id')
    if not interaction_id:
        # Fallback to name if id not present (future proofing)
        interaction_id = data.get('name')
        
    if not interaction_id:
        print("No interaction ID returned.")
        exit(1)

    print(f"\nInteraction started: {interaction_id}")
    
    # Polling loop
    poll_url = f"{base_url}/{interaction_id}"
    
    print(f"Polling {poll_url}...")
    
    # Poll for up to 5 minutes
    for i in range(30):
        time.sleep(10)
        print(f"\nPolling attempt {i+1}...")
        poll_resp = requests.get(poll_url, headers=headers)
        
        if poll_resp.status_code != 200:
            print(f"Poll Error: {poll_resp.status_code} - {poll_resp.text}")
            continue
            
        poll_data = poll_resp.json()
        
        # Check status (API returned 'status' field, not 'state')
        status = poll_data.get('status')
        if not status:
            status = poll_data.get('state', 'UNKNOWN')
            
        print(f"Status: {status}")
        
        if status == 'completed' or status == 'COMPLETED':
            print("\n=== COMPLETED ===\n")
            # The output location might vary, checking expected likely paths
            if 'outputs' in poll_data and poll_data['outputs']:
                 # Check if it's a list of objects with 'text' or just strings
                 last_output = poll_data['outputs'][-1]
                 if isinstance(last_output, dict):
                     print(last_output.get('text', str(last_output)))
                 else:
                     print(str(last_output))
            else:
                print(json.dumps(poll_data, indent=2))
            break
        elif status == 'failed' or status == 'FAILED':
            print("\n=== FAILED ===\n")
            print(json.dumps(poll_data, indent=2))
            break

except Exception as e:
    print(f"Exception: {e}")
