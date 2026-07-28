import sqlite3

conn = sqlite3.connect('paper_matcher.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cnt = cur.execute("SELECT COUNT(*) FROM acl_papers WHERE event_year = '2026' AND paper_key LIKE '%.acl-long.%'").fetchone()[0]
print(f"ACL Long query match count: {cnt}")

rows = cur.execute("SELECT id, paper_key, title FROM acl_papers WHERE event_year = '2026' AND paper_key LIKE '%.acl-long.%' LIMIT 5").fetchall()
for r in rows:
    print(f" - [{r['id']}] {r['paper_key']} -> {r['title']}")
