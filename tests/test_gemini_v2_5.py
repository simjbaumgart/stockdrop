import sys
import os
import time
from dotenv import load_dotenv

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

from app.services.research_service import ResearchService

def test_gemini_2_5():
    print("Initializing ResearchService...")
    service = ResearchService()
    
    if not service.flash_model:
        print("ERROR: Flash Model (Gemini 2.5) not initialized. Check GEMINI_API_KEY.")
        return

    print("Target Model Name:", service.flash_model._model_name) 
    # Note: Accessing private attribute _model_name might vary by SDK version, 
    # so we might just trust the init.

    prompt = "Explain in one sentence why testing the latest AI model is important for a trading bot."
    
    print(f"\nSending prompt to Economics Agent (should use Gemini 2.5): '{prompt}'")
    
    start_time = time.time()
    response = service._call_agent(prompt, "Economics Agent")
    elapsed = time.time() - start_time
    
    print(f"\n[Response Received in {elapsed:.2f}s]:")
    print(response)
    
    print("\nTest Complete.")

if __name__ == "__main__":
    test_gemini_2_5()
