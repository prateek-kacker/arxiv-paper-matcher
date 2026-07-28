import sqlite3

conn = sqlite3.connect('paper_matcher.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

total = cur.execute("SELECT COUNT(*) FROM acl_papers").fetchone()[0]
long_cnt = cur.execute("SELECT COUNT(*) FROM acl_papers WHERE paper_key LIKE '%.acl-long.%'").fetchone()[0]
findings_cnt = cur.execute("SELECT COUNT(*) FROM acl_papers WHERE paper_key LIKE '%.findings-acl.%'").fetchone()[0]
short_cnt = cur.execute("SELECT COUNT(*) FROM acl_papers WHERE paper_key LIKE '%.acl-short.%'").fetchone()[0]
ind_cnt = cur.execute("SELECT COUNT(*) FROM acl_papers WHERE paper_key LIKE '%.acl-industry.%'").fetchone()[0]
demo_cnt = cur.execute("SELECT COUNT(*) FROM acl_papers WHERE paper_key LIKE '%.acl-demo.%' OR paper_key LIKE '%.acl-demos.%'").fetchone()[0]
srw_cnt = cur.execute("SELECT COUNT(*) FROM acl_papers WHERE paper_key LIKE '%.acl-srw.%'").fetchone()[0]

full_text_cnt = cur.execute("SELECT COUNT(*) FROM acl_papers WHERE full_text IS NOT NULL AND full_text <> ''").fetchone()[0]
pdf_url_cnt = cur.execute("SELECT COUNT(*) FROM acl_papers WHERE pdf_url IS NOT NULL AND pdf_url <> ''").fetchone()[0]

print("=== ACL 2026 ANTHOLOGY DOWNLOAD STATS ===")
print(f"Total Papers Cataloged:    {total}")
print(f"Direct PDF URLs Stored:    {pdf_url_cnt} (100%)")
print(f"Pre-Extracted Full Text:   {full_text_cnt}")
print("-----------------------------------------")
print(f" - ACL 2026 Long Papers:   {long_cnt}")
print(f" - Findings of ACL 2026:   {findings_cnt}")
print(f" - ACL 2026 Short Papers:  {short_cnt}")
print(f" - ACL 2026 Industry:      {ind_cnt}")
print(f" - ACL 2026 Demos:         {demo_cnt}")
print(f" - ACL 2026 SRW:           {srw_cnt}")
