import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core_engine as core

core.init_db()

conn = core.get_db()
cur = conn.cursor()

total = cur.execute("SELECT COUNT(*) FROM acl_papers").fetchone()[0]
with_abstract = cur.execute("SELECT COUNT(*) FROM acl_papers WHERE abstract IS NOT NULL AND abstract <> ''").fetchone()[0]
with_full_text = cur.execute("SELECT COUNT(*) FROM acl_papers WHERE full_text IS NOT NULL AND full_text <> ''").fetchone()[0]
with_pdf = cur.execute("SELECT COUNT(*) FROM acl_papers WHERE pdf_url IS NOT NULL AND pdf_url <> ''").fetchone()[0]

print(f"Total ACL Papers in DB:          {total}")
print(f"Papers with Abstracts:           {with_abstract}")
print(f"Papers with PDF URLs:            {with_pdf}")
print(f"Papers with Stored Full Text:    {with_full_text}")

row = cur.execute("SELECT id, paper_key, title, abstract, pdf_url FROM acl_papers LIMIT 1").fetchone()
if row:
    print("\nSample ACL Paper Record:")
    print(f"  ID:         {row['id']}")
    print(f"  Key:        {row['paper_key']}")
    print(f"  Title:      {row['title']}")
    print(f"  PDF URL:    {row['pdf_url']}")
    print(f"  Abstract:   {row['abstract'][:150]}...")
