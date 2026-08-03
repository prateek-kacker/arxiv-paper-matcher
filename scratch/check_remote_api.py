import urllib.request
import json

base_url = "https://archive-paper-matcher-gr6ge7htzq-uc.a.run.app"

try:
    print("Querying /api/evaluations...")
    req = urllib.request.Request(f"{base_url}/api/evaluations")
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
        evals = data.get("evaluations", [])
        print(f"Total evaluations in Cloud Run DB: {len(evals)}")
        for e in evals[:15]:
            print("  Eval:", e)
            
    print("\nQuerying /api/evaluations/36...")
    req36 = urllib.request.Request(f"{base_url}/api/evaluations/36")
    with urllib.request.urlopen(req36, timeout=10) as resp:
        data36 = json.loads(resp.read().decode())
        print(f"Eval 36 response: eval_id={data36.get('eval_id')}, total papers={len(data36.get('papers', []))}")
        for p in data36.get("papers", [])[:5]:
            print(f"   Paper ID: {p.get('id')}, Score: {p.get('avg_score')}, Title: {p.get('title')[:60]}")

except urllib.error.HTTPError as e:
    print(f"HTTP Error: {e.code} - {e.reason}")
    print(e.read().decode())
except Exception as exc:
    print(f"Error querying API: {exc}")
