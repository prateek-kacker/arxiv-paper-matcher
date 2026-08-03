import sqlite3
import urllib.request
import json

base_url = "https://archive-paper-matcher-gr6ge7htzq-uc.a.run.app"

try:
    print("Querying /api/evaluations...")
    with urllib.request.urlopen(f"{base_url}/api/evaluations") as resp:
        data = json.loads(resp.read().decode())
        evals = data.get("evaluations", [])
        for e in evals:
            print("Eval:", e)
except Exception as exc:
    print("Error:", exc)
