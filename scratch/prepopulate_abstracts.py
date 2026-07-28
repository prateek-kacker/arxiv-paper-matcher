import asyncio
import httpx
import re
import sqlite3
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core_engine as core

async def fetch_abstract(client, pid, url, title):
    try:
        resp = await client.get(url, timeout=4.0)
        if resp.status_code == 200:
            m = re.search(r'class="card-body acl-abstract"[^>]*>(.*?)</div>', resp.text, re.DOTALL)
            if m:
                abstract = re.sub(r'<[^>]+>', '', m.group(1)).replace('Abstract', '', 1).strip()
                return pid, abstract
    except Exception:
        pass
    return pid, title

async def batch_prepopulate():
    conn = core.get_db()
    rows = conn.execute("SELECT id, url, title FROM acl_papers WHERE abstract IS NULL OR abstract = '' LIMIT 300").fetchall()
    print(f"Pre-populating abstracts for {len(rows)} ACL papers...")
    
    if not rows:
        print("All papers already have abstracts!")
        conn.close()
        return

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        sem = asyncio.Semaphore(15)
        
        async def worker(r):
            async with sem:
                return await fetch_abstract(client, r["id"], r["url"], r["title"])
                
        results = await asyncio.gather(*[worker(r) for r in rows])
        
    updated = 0
    for pid, abstract in results:
        if abstract and len(abstract) > 30:
            conn.execute("UPDATE acl_papers SET abstract = ? WHERE id = ?", (abstract, pid))
            updated += 1
    conn.commit()
    conn.close()
    print(f"Successfully updated {updated} paper abstracts in SQLite database!")

if __name__ == '__main__':
    asyncio.run(batch_prepopulate())
