import sqlite3

conn = sqlite3.connect("paper_matcher.db")
cursor = conn.cursor()

count = cursor.execute("SELECT COUNT(*) FROM acl_papers").fetchone()[0]
print("Total rows in acl_papers:", count)

if count > 0:
    sample = cursor.execute("SELECT id, paper_key, title, event_year FROM acl_papers LIMIT 5").fetchall()
    print("Sample rows:", sample)

tracks = cursor.execute("SELECT paper_key FROM acl_papers").fetchall()
print("Sample paper_keys:", [t[0] for t in tracks[:10]])

conn.close()
