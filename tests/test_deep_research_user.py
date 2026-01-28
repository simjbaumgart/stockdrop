import time
import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

# 1. Setup Client
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    # Fallback/Debug
    print("GEMINI_API_KEY not found in environment, checking other keys...")
    # Just in case it's under a different name, but for now let's error out if missing
    raise ValueError("GEMINI_API_KEY not found in environment")

print(f"Using API Key: {api_key[:5]}...")

client = genai.Client(api_key=api_key)

# 2. Start the Deep Research (Async/Background)
# We use the specific agent name 'deep-research-pro-preview-12-2025'
print("Starting research agent...")
try:
    interaction = client.interactions.create(
        agent='deep-research-pro-preview-12-2025',
        input="Analyze the current competitive landscape of EV batteries in Europe for 2025. Focus on supply chain risks.",
        background=True  # REQUIRED: This allows the task to run for minutes
    )
except Exception as e:
    print(f"Error creating interaction: {e}")
    exit(1)

print(f"Research Job Started! ID: {interaction.id}")

# 3. Poll for Results
# In a real app, you would save the ID and check later.
while True:
    # Retrieve the latest status of this interaction
    try:
        job_status = client.interactions.get(interaction.id)
    except Exception as e:
        print(f"Error getting status: {e}")
        time.sleep(10)
        continue
    
    state = job_status.state # e.g., 'PROCESSING', 'COMPLETED', 'FAILED'
    print(f"Status: {state}")
    
    if state == "COMPLETED":
        # The final report is in the last output message
        print("\n=== FINAL REPORT ===\n")
        # Check if outputs exist
        if job_status.outputs:
            print(job_status.outputs[-1].text)
        else:
            print("No output text found.")
        break
    elif state == "FAILED":
        print("Research failed.")
        if hasattr(job_status, 'error'):
            print(f"Error details: {job_status.error}")
        break
    
    time.sleep(10) # Wait 10 seconds before checking again
