import urllib.request
import re
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core_engine as core

def scrape_all_acl_2026():
    print("--- Scraping ALL 2000+ papers for ACL 2026 ---")
    url = "https://aclanthology.org/events/acl-2026/"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
    
    try:
        html = urllib.request.urlopen(req).read().decode('utf-8')
        print(f"ACL 2026 HTML length: {len(html)}")
        
        # Regex to capture all paper titles & links across all volumes in ACL 2026
        # Matches: <a class=align-middle href=/2026.acl-long.1/>Title</a> or quoted hrefs
        pattern = r'href=["\']?(/2026\.[a-zA-Z0-9\-_]+\.\d+/?)"?[\s>][^>]*>(.*?)</a>'
        raw_matches = re.findall(pattern, html)
        
        papers_map = {}
        for href, title in raw_matches:
            clean_href = href.strip('/')
            paper_key = clean_href.split('/')[-1]
            clean_title = re.sub(r'<[^>]+>', '', title).strip()
            
            # Avoid volume headers like "Proceedings of..."
            if clean_title and not clean_title.startswith("Proceedings of the") and not clean_title.startswith("Findings of the"):
                p_url = f"https://aclanthology.org/{paper_key}/"
                pdf_url = f"https://aclanthology.org/{paper_key}.pdf"
                papers_map[paper_key] = {
                    "title": clean_title,
                    "url": p_url,
                    "pdf_url": pdf_url,
                }
                
        print(f"Found {len(papers_map)} unique ACL 2026 papers across all volumes!")
        
        conn = core.get_db()
        # Clean 2024 and 2025 papers as requested by user
        conn.execute("DELETE FROM acl_papers WHERE event_year != '2026'")
        conn.commit()
        print("Removed 2024 and 2025 papers from local database.")

        inserted = 0
        for pkey, pdata in papers_map.items():
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO acl_papers (event_year, paper_key, title, authors, abstract, url, pdf_url, published)
                       VALUES ('2026', ?, ?, ?, ?, ?, ?, '2026-08')""",
                    (pkey, pdata["title"], "ACL 2026 Authors", "", pdata["url"], pdata["pdf_url"])
                )
                inserted += 1
            except Exception:
                pass
        conn.commit()
        
        count = conn.execute("SELECT COUNT(*) FROM acl_papers WHERE event_year = '2026'").fetchone()[0]
        conn.close()
        print(f"Total ACL 2026 papers stored in database: {count}")
        
    except Exception as err:
        print(f"Error scraping ACL 2026: {err}")

if __name__ == '__main__':
    core.init_db()
    scrape_all_acl_2026()
