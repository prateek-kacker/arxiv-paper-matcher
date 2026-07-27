import urllib.request
import re
import sqlite3
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core_engine as core

def prefetch_acl_year(year="2024"):
    print(f"--- Prefetching ACL {year} Long Papers ---")
    url = f"https://aclanthology.org/events/acl-{year}/"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
    
    try:
        html = urllib.request.urlopen(req).read().decode('utf-8')
        matches = re.findall(r'href=(/[0-9]{4}\.acl-long\.\d+/)>([^<]+)</a>', html)
        if not matches:
            matches = re.findall(r'href=["\'](/[^"\']*acl-long\.\d+/?)["\'][^>]*>([^<]+)</a>', html)
        
        print(f"Found {len(matches)} ACL {year} Long paper entries!")
        conn = core.get_db()
        inserted = 0
        for href, title in matches:
            paper_key = href.strip('/').split('/')[-1]
            p_url = f"https://aclanthology.org{href}"
            pdf_url = f"https://aclanthology.org{href.rstrip('/')}.pdf"

            try:
                conn.execute(
                    """INSERT OR IGNORE INTO acl_papers (event_year, paper_key, title, authors, abstract, url, pdf_url, published)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (year, paper_key, title.strip(), "ACL Authors", "", p_url, pdf_url, f"{year}-08")
                )
                inserted += 1
            except Exception as e:
                pass
        conn.commit()
        conn.close()
        print(f"Successfully cached {inserted} ACL {year} paper records into database!")
    except Exception as err:
        print(f"Error fetching ACL {year}: {err}")

if __name__ == '__main__':
    core.init_db()
    for y in ["2024", "2025", "2026"]:
        prefetch_acl_year(y)
    
    print("\nSyncing updated database to Google Cloud Storage...")
    ok, msg = core.sync_db_to_cloud()
    print("Cloud Sync Result:", msg)
