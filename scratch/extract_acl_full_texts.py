import asyncio
import httpx
import fitz  # PyMuPDF
import sqlite3
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core_engine as core

async def fetch_pdf_text(client, pid, pdf_url):
    try:
        resp = await client.get(pdf_url, timeout=8.0)
        if resp.status_code == 200:
            doc = fitz.open(stream=resp.content, filetype="pdf")
            text_pages = []
            for page in doc:
                text_pages.append(page.get_text())
            full_text = "\n".join(text_pages).strip()
            if len(full_text) > 200:
                return pid, full_text
    except Exception as e:
        pass
    return pid, None

async def extract_batch(limit: int = 100):
    core.init_db()
    conn = core.get_db()
    rows = conn.execute(
        "SELECT id, pdf_url FROM acl_papers WHERE pdf_url IS NOT NULL AND (full_text IS NULL OR full_text = '') LIMIT ?",
        (limit,)
    ).fetchall()
    
    print(f"Extracting full PDF text for {len(rows)} ACL papers...")
    if not rows:
        print("No pending ACL papers to extract.")
        conn.close()
        return

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        sem = asyncio.Semaphore(25)
        
        async def worker(r):
            async with sem:
                return await fetch_pdf_text(client, r["id"], r["pdf_url"])
                
        results = await asyncio.gather(*[worker(r) for r in rows])

    updated = 0
    for pid, full_text in results:
        if full_text:
            conn.execute("UPDATE acl_papers SET full_text = ? WHERE id = ?", (full_text, pid))
            updated += 1

    conn.commit()
    conn.close()
    print(f"SUCCESS: Extracted and stored full PDF text for {updated}/{len(rows)} ACL papers into SQLite!")

if __name__ == '__main__':
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    asyncio.run(extract_batch(limit))
