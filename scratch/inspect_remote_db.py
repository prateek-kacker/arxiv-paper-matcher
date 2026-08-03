import urllib.request
import json

base_url = "https://archive-paper-matcher-gr6ge7htzq-uc.a.run.app"

try:
    print("Querying /api/config...")
    with urllib.request.urlopen(f"{base_url}/api/config") as resp:
        print("Config:", json.loads(resp.read().decode()))

    print("\nQuerying /api/cloud-sync/status...")
    with urllib.request.urlopen(f"{base_url}/api/cloud-sync/status") as resp:
        print("Sync Status:", json.loads(resp.read().decode()))

    print("\nQuerying /api/all-papers...")
    with urllib.request.urlopen(f"{base_url}/api/all-papers") as resp:
        papers = json.loads(resp.read().decode()).get("papers", [])
        print(f"Total papers across all evals: {len(papers)}")
        eval_ids_in_papers = set(p.get("evaluation_id") for p in papers)
        print("Evaluation IDs present in papers table:", eval_ids_in_papers)

except Exception as e:
    print("Error:", e)
