import requests
import os
import json
import time
import argparse
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def run_deep_research(prompt, api_key=None):
    """
    Executes a Deep Research task using the Google Gemini REST API.
    """
    if not api_key:
        api_key = os.getenv("GEMINI_API_KEY")
    
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in environment or arguments.")

    # Base URL for the Interactions API
    base_url = "https://generativelanguage.googleapis.com/v1beta/interactions"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key
    }

    # Payload
    payload = {
        "input": prompt,
        "agent": "deep-research-pro-preview-12-2025",
        "background": True
    }

    print(f"Starting research on: '{prompt}'")
    
    try:
        # 1. Create the Interaction
        response = requests.post(base_url, headers=headers, json=payload)
        
        if response.status_code != 200:
            print(f"Error creating interaction: {response.text}")
            return None

        data = response.json()
        interaction_id = data.get('id') or data.get('name')
        
        if not interaction_id:
            print("Error: No interaction ID returned.")
            return None

        print(f"Job ID: {interaction_id}")
        
        # 2. Poll for results
        poll_url = f"{base_url}/{interaction_id}"
        print(f"Polling status...")
        
        start_time = time.time()
        while True:
            # Wait before polling
            time.sleep(10)
            
            poll_resp = requests.get(poll_url, headers=headers)
            if poll_resp.status_code != 200:
                print(f"Poll Error: {poll_resp.status_code}")
                continue
            
            poll_data = poll_resp.json()
            
            # Check status
            status = poll_data.get('status') 
            if not status:
                status = poll_data.get('state', 'UNKNOWN')
            
            elapsed = int(time.time() - start_time)
            print(f"Status: {status} (T+{elapsed}s)")
            
            if status in ['completed', 'COMPLETED']:
                print("\n=== RESEARCH COMPLETED ===")
                outputs = poll_data.get('outputs', [])
                if outputs:
                    # Usually the last output is the final report
                    final_output = outputs[-1]
                    if isinstance(final_output, dict):
                        return final_output.get('text', '')
                    else:
                        return str(final_output)
                else:
                    return "No output text found."
            
            elif status in ['failed', 'FAILED']:
                print("\n=== RESEARCH FAILED ===")
                if 'error' in poll_data:
                    return f"Error: {poll_data['error']}"
                return "Unknown error"
                
    except Exception as e:
        print(f"Exception occurred: {e}")
        return None

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Deep Research Agent")
    parser.add_argument("prompt", type=str, help="Research topic or question")
    args = parser.parse_args()
    
    report = run_deep_research(args.prompt)
    if report:
        print("\n\n" + report)
