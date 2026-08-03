import sqlite3
import json
import sys
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')
sys.path.append(str(Path(__file__).parent.parent))

import core_engine as core

print("=== Inspecting Database Schedules & Due Status ===")
print(f"Database Path: {core.DB_PATH}")
print(f"Current System Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"Current UTC Time:    {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}")

# Try syncing from GCS bucket if set
ok, msg = core.sync_db_from_cloud()
print(f"Cloud Sync Status: {ok} — {msg}\n")

conn = core.get_db()
schedules = [dict(r) for r in conn.execute("SELECT * FROM recurring_schedules").fetchall()]
conn.close()

print(f"Found {len(schedules)} recurring schedule(s) in DB:\n")
for s in schedules:
    print(f"ID: {s['id']} | Label: {s['label']}")
    print(f"  - Configured Run Time: {s['run_time']} (Server Time)")
    print(f"  - Active: {s['is_active']}")
    print(f"  - Last Run Date: {s['last_run_date']}")
    print(f"  - Last Run At: {s['last_run_at']}")
    print(f"  - Last Status: {s['last_status']}")
    print(f"  - Last Message: {s['last_message']}")
    print(f"  - Research Problem: {s['problem_text'][:60]}...")
    print("-" * 60)

print("\n=== Checking load_due_recurring_schedules() ===")
due = core.load_due_recurring_schedules()
print(f"Due schedules right now: {len(due)}")
for d in due:
    print(f"  - Due Schedule ID #{d['id']}: {d['label']} (Run Time: {d['run_time']})")
