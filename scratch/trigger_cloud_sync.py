import urllib.request
import json

base_url = "https://archive-paper-matcher-gr6ge7htzq-uc.a.run.app"

try:
    print("Triggering Cloud Sync on Cloud Run...")
    req = urllib.request.Request(f"{base_url}/api/cloud-sync/trigger", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        res = json.loads(resp.read().decode())
        print("Trigger sync response:", res)

    print("\nQuerying /api/evaluations/36 after sync...")
    req36 = urllib.request.Request(f"{base_url}/api/evaluations/36")
    with urllib.request.urlopen(req36, timeout=10) as resp:
        data36 = json.loads(resp.read().decode())
        papers = data36.get("papers", [])
        print(f"Eval 36 papers count: {len(papers)}")
        for p in papers:
            print(f"  - [{p.get('avg_score')}/10] {p.get('title')}")

except Exception as e:
    print("Error:", e)
