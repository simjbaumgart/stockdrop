from google import genai
import os
import sys
from dotenv import load_dotenv

load_dotenv()


def test_grounding():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY not found in environment.")
        return

    print("Initializing Google GenAI Client...")
    try:
        client = genai.Client(api_key=api_key)
    except Exception as e:
        print(f"Failed to initialize client: {e}")
        return

    model_name = "gemini-3-pro-preview"
    prompt = "What is the latest stock price of Apple (AAPL) and why is it moving today?"
    
    print(f"Calling {model_name} with Grounding...")
    
    try:
        config = {
            "tools": [{"google_search": {}}],
            "temperature": 0.7
        }
        
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=config
        )
        
        if response.candidates:
            print("Response received successfully!")
            print("-" * 50)
            print(response.text[:500] + "...")
            print("-" * 50)
            
            # Check for grounding metadata presence if possible
            # (Details depend on the SDK version, but successful text implies success)
        else:
             print("Response received but no candidates found.")

    except Exception as e:
        print(f"\n[ERROR] Grounding Call Failed: {e}")
        # Print detailed traceback if it's a socket error
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_grounding()
