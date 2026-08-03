import os

api_key = os.environ.get("GEMINI_API_KEY", "").strip()
print("GEMINI_API_KEY present:", bool(api_key))
if api_key:
    print("API Key prefix:", api_key[:6] + "...")
