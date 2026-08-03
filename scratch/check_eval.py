import sqlite3
import os
import glob

db_path = "paper_matcher.db"
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    tables = [row[0] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print("Tables in paper_matcher.db:", tables)
    
    for table in tables:
        try:
            cols = [info[1] for info in cursor.execute(f"PRAGMA table_info({table})").fetchall()]
            # check if any string column contains eval36
            for col in cols:
                query = f"SELECT * FROM {table} WHERE CAST({col} AS TEXT) LIKE '%eval36%' OR CAST({col} AS TEXT) LIKE '%36%'"
                res = cursor.execute(query).fetchall()
                if res and 'eval36' in str(res):
                    print(f"Match in {table}.{col}: {len(res)} rows")
        except Exception as e:
            pass
    conn.close()

# Also search for files named *eval* or containing eval36
for root, dirs, files in os.walk("."):
    if ".venv" in root or ".git" in root:
        continue
    for f in files:
        if "eval" in f.lower() or "36" in f.lower():
            print("Found file:", os.path.join(root, f))
