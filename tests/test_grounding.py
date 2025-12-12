import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.research_service import research_service
from dotenv import load_dotenv

load_dotenv()

def test_grounding():
    print("Testing Grounding Integration...")
    
    if not research_service.grounding_client:
        print("ERROR: Grounding Client not initialized.")
        return

    prompt = "Who won the Euro 2024 final and what was the score?"
    print(f"\nPrompt: {prompt}")
    
    try:
        response = research_service._call_news_agent_with_grounding(prompt)
        print("\n--- Response ---")
        print(response)
        print("\n----------------")
        
        if "Grounding Sources" in response:
            print("SUCCESS: Citations found in response.")
        else:
            print("WARNING: No citations found (might be expected if grounding didn't trigger or failed).")
            
    except Exception as e:
        print(f"ERROR: Test failed with exception: {e}")

if __name__ == "__main__":
    test_grounding()
