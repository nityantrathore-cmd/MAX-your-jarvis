import concurrent.futures
import time

import requests

API_KEY = "api here"
BASE = "https://generativelanguage.googleapis.com/v1beta"
HEADERS = {"x-goog-api-key": API_KEY, "Content-Type": "application/json"}
MODELS = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash",
          "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-latest"]
BODY = {"contents": [{"role": "user", "parts": [{"text": "Say hi in two words."}]}],
        "generationConfig": {"maxOutputTokens": 50}}

if API_KEY.startswith("PASTE"):
    raise SystemExit("Paste your Gemini key into API_KEY first.")


def post(model):
    t = time.time()
    try:
        r = requests.post(f"{BASE}/models/{model}:generateContent", headers=HEADERS, json=BODY, timeout=30)
        return model, r.status_code, time.time() - t
    except Exception as e:
        return model, type(e).__name__, time.time() - t


print("1) Key check (GET, list models):")
t = time.time()
try:
    r = requests.get(f"{BASE}/models?pageSize=1", headers=HEADERS, timeout=15)
    print(f"   HTTP {r.status_code} in {time.time() - t:.1f}s")
except Exception as e:
    print(f"   FAILED -> {type(e).__name__}")

print("\n2) POST sanity check (a model that does not exist should answer 404 quickly):")
m, status, secs = post("model-that-does-not-exist")
print(f"   {status} in {secs:.1f}s")
if status == 404:
    print("   -> POST requests reach Google fine.")
else:
    print("   -> POST requests are being blocked or stalled (network / antivirus / firewall).")

print("\n3) Testing models at the same time (up to 30s)...")
with concurrent.futures.ThreadPoolExecutor() as pool:
    results = list(pool.map(post, MODELS))

good = []
for model, status, secs in results:
    print(f"   {model:24s} {status}  {secs:.1f}s")
    if status == 200:
        good.append((secs, model))

print("\nRESULT:")
if good:
    best = min(good)[1]
    print(f'   Fastest working model: {best}')
    print(f'   In gemini_assistant.py set:  MODEL = "{best}"')
else:
    print("   No model answered. Paste this whole output to me.")
