import google.generativeai as genai
import os
from dotenv import load_dotenv

# Load .env explicitly
load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("Error: GEMINI_API_KEY not found in environment.")
    exit(1)

print(f"Using API Key: {api_key[:5]}...{api_key[-5:]}")

try:
    genai.configure(api_key=api_key)
    print("Listing models...")
    models = list(genai.list_models())
    print(f"Total models found: {len(models)}")

    print("\n--- Models with 'deep' in name ---")
    deep_models = [m.name for m in models if 'deep' in m.name.lower()]
    print(deep_models)

    print("\n--- Models with 'thinking' in name ---")
    thinking_models = [m.name for m in models if 'thinking' in m.name.lower()]
    print(thinking_models)

    print("\n--- Models with 'gemini-3' in name ---")
    gemini3_models = [m.name for m in models if 'gemini-3' in m.name.lower()]
    print(gemini3_models)
    
    print("\n--- All Gemini Models ---")
    for m in models:
        if 'gemini' in m.name.lower():
            print(m.name)

except Exception as e:
    print(f"Error listing models: {e}")
