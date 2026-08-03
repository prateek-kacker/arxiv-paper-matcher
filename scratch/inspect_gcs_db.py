import sqlite3
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

db_path = Path("scratch/gcs_paper_matcher.db")
conn = sqlite3.connect(str(db_path))
conn.row_factory = sqlite3.Row

print("=== GCS Production Database Inspection ===")

schedules = [dict(r) for r in conn.execute("SELECT * FROM recurring_schedules").fetchall()]
print(f"Total Recurring Schedules in GCS DB: {len(schedules)}\n")

for s in schedules:
    print(f"ID: #{s['id']} | Label: '{s['label']}'")
    print(f"  - Configured Run Time: {s['run_time']}")
    print(f"  - Is Active: {s['is_active']}")
    print(f"  - Last Run Date: {s['last_run_date']}")
    print(f"  - Last Run At: {s['last_run_at']}")
    print(f"  - Last Status: {s['last_status']}")
    print(f"  - Last Message: {s['last_message']}")
    print(f"  - Last Eval ID: {s.get('last_eval_id')}")
    print(f"  - Problem: {s['problem_text'][:70]}...")
    print("-" * 65)

print("\n=== Recent Evaluations in GCS DB ===")
evals = [dict(r) for r in conn.execute("SELECT * FROM evaluations ORDER BY id DESC LIMIT 10").fetchall()]
for e in evals:
    print(f"Eval #{e['id']} | Created At: {e['created_at']} | Model: {e['model_name']} | Status: {e.get('status')}")
conn.close()
