import sqlite3

conn = sqlite3.connect("paper_matcher.db")
cursor = conn.cursor()

tables = [r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("Tables:", tables)

for table in tables:
    print(f"\n--- Table: {table} ---")
    cursor.execute(f"PRAGMA table_info({table})")
    cols = [col[1] for col in cursor.fetchall()]
    print("Columns:", cols)
    
    # Check if id = 36 or evaluation_id = 36 or schedule_id = 36
    for c in cols:
        try:
            res = cursor.execute(f"SELECT * FROM {table} WHERE CAST({c} AS TEXT) = '36' OR CAST({c} AS TEXT) LIKE '%36%' LIMIT 5").fetchall()
            if res:
                print(f"Matches in {table} where {c} contains 36:")
                for r in res:
                    print("  ", r)
        except Exception as e:
            pass

print("\n--- Recent Evaluations ---")
if "evaluations" in tables:
    evals = cursor.execute("SELECT * FROM evaluations ORDER BY id DESC LIMIT 10").fetchall()
    for e in evals:
        print("  ", e)

if "evaluation_runs" in tables:
    evals = cursor.execute("SELECT * FROM evaluation_runs ORDER BY id DESC LIMIT 10").fetchall()
    for e in evals:
        print("  ", e)

if "schedules" in tables or "recurring_schedules" in tables:
    t_sch = "schedules" if "schedules" in tables else "recurring_schedules"
    schs = cursor.execute(f"SELECT * FROM {t_sch} WHERE id = 36 OR label LIKE '%36%'").fetchall()
    print("\n--- Schedules matching 36 ---")
    for s in schs:
        print("  ", s)

conn.close()
