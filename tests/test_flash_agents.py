
import sys
import os
import time
from dotenv import load_dotenv

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

from app.services.research_service import ResearchService

def test_agents():
    print("Initializing ResearchService...")
    service = ResearchService()
    
    if not service.flash_model:
        print("ERROR: Flash Model (Gemini 2.5) not initialized.")
        return

    print("Flash Model Name:", service.flash_model._model_name)

    # 1. Test Technical Agent
    print("\n--- Testing Technical Agent ---")
    try:
        # Technical Agent relies on _call_agent special handling
        prompt = "Briefly describe the purpose of RSI."
        # We can mock the model to print something or just check logs. 
        # But we want to see if it runs.
        res = service._call_agent(prompt, "Technical Agent")
        print(f"Result (First 50 chars): {res[:50]}...")
    except Exception as e:
        print(f"Technical Agent Failed: {e}")

    # 2. Test News Agent
    print("\n--- Testing News Agent (Grounding) ---")
    try:
        # News Agent uses _call_agent -> _call_news_agent_with_grounding
        # It requires grounding client.
        if not service.grounding_client:
            print("Skipping News Agent: No Grounding Client.")
        else:
            prompt = "What is the latest news for Apple (AAPL)? Summarize in 1 sentence."
            res = service._call_agent(prompt, "News Agent")
            print(f"Result: {res}")
    except Exception as e:
        print(f"News Agent Failed: {e}")

    # 3. Test Competitive Agent
    print("\n--- Testing Competitive Agent (Grounding) ---")
    try:
        # Competitive Agent uses _call_agent -> _call_competitive_agent (via our new dispatch)
        if not service.grounding_client:
             print("Skipping Competitive Agent: No Grounding Client.")
        else:
             prompt = "Who are Apple's competitors?"
             res = service._call_agent(prompt, "Competitive Landscape Agent")
             print(f"Result (First 100 chars): {res[:100]}...")
    except Exception as e:
         print(f"Competitive Agent Failed: {e}")


    # 4. Test Economics Agent (Verified Grounding)
    print("\n--- Testing Economics Agent (Grounding) ---")
    try:
        # Should now use grounding
        prompt = "What is the current US Interest Rate?"
        res = service._call_agent(prompt, "Economics Agent")
        print(f"Result (First 100 chars): {res[:100]}...")
        if "Grounding: Enabled" in res:
             print("SUCCESS: Grounding Enabled confirmed.")
        else:
             print("WARNING: Grounding signature missing.")
    except Exception as e:
        print(f"Economics Agent Failed: {e}")

    # 5. Test Technical Agent (Verified Grounding)
    print("\n--- Testing Technical Agent (Grounding) ---")
    try:
         prompt = "What is the RSI of AAPL today?"
         res = service._call_agent(prompt, "Technical Agent")
         print(f"Result (First 100 chars): {res[:100]}...")
         if "Grounding: Enabled" in res:
              print("SUCCESS: Grounding Enabled confirmed.")
         else:
              print("WARNING: Grounding signature missing.")
    except Exception as e:
         print(f"Technical Agent Failed: {e}")

    # 6. Test Bull Researcher (Verified Grounding & Model Check)
    print("\n--- Testing Bull Researcher (Grounding + Gemini 3 Pro) ---")
    try:
         prompt = "Argue why one should buy Nvidia stock."
         res = service._call_agent(prompt, "Bull Researcher")
         print(f"Result (First 100 chars): {res[:100]}...")
         
         if "Grounding: Enabled" in res:
              print("SUCCESS: Grounding Enabled confirmed.")
         else:
              print("WARNING: Grounding signature missing.")

         if "Model: gemini-3-pro-preview" in res:
              print("SUCCESS: Model is Gemini 3 Pro Preview.")
         else:
              print(f"WARNING: Incorrect model used. Output: {res[-100:]}")
              
    except Exception as e:
         print(f"Bull Researcher Failed: {e}")

if __name__ == "__main__":
    test_agents()
