import os
from dotenv import load_dotenv

load_dotenv()

print(f"GEMINI_API_KEY set: {bool(os.getenv('GEMINI_API_KEY'))}")
print(f"SENDER_EMAIL set: {bool(os.getenv('SENDER_EMAIL'))}")
