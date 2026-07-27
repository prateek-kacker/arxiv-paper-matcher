import sqlite3

conn = sqlite3.connect('paper_matcher.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

eval_count = cur.execute('SELECT COUNT(*) FROM evaluations').fetchone()[0]
paper_count = cur.execute('SELECT COUNT(*) FROM papers').fetchone()[0]

try:
    acl_count = cur.execute('SELECT COUNT(*) FROM acl_papers').fetchone()[0]
    acl_with_abs = cur.execute("SELECT COUNT(*) FROM acl_papers WHERE abstract IS NOT NULL AND abstract != ''").fetchone()[0]
    years = cur.execute("SELECT event_year, COUNT(*) as cnt FROM acl_papers GROUP BY event_year").fetchall()
except Exception as e:
    acl_count = 0
    acl_with_abs = 0
    years = []

print(f"Total Evaluation Runs: {eval_count}")
print(f"Total Evaluated Papers: {paper_count}")
print(f"Total ACL Anthology Papers Cached: {acl_count}")
print(f"ACL Papers with Full Abstract Text Extracted: {acl_with_abs}")
for y in years:
    print(f"  - ACL {y['event_year']}: {y['cnt']} papers")
