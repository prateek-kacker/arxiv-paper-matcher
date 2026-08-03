import sqlite3
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core_engine import fetch_acl_papers

print("--- Testing fetch_acl_papers ---")

# 1. No keyword, all tracks
p1 = fetch_acl_papers(max_results=None, search_query="", volume_filter="all")
print("1. volume_filter='all', keyword='':", len(p1), "papers returned")

# 2. Keyword 'reasoning', all tracks
p2 = fetch_acl_papers(max_results=None, search_query="reasoning", volume_filter="all")
print("2. volume_filter='all', keyword='reasoning':", len(p2), "papers returned")

# 3. Keyword 'feedback', all tracks
p3 = fetch_acl_papers(max_results=None, search_query="feedback", volume_filter="all")
print("3. volume_filter='all', keyword='feedback':", len(p3), "papers returned")

# 4. Keyword 'continuous learning', all tracks
p4 = fetch_acl_papers(max_results=None, search_query="continuous learning", volume_filter="all")
print("4. volume_filter='all', keyword='continuous learning':", len(p4), "papers returned")

# 5. Check if max_results=10 was passed anywhere
p5 = fetch_acl_papers(max_results=10, search_query="", volume_filter="all")
print("5. max_results=10:", len(p5), "papers returned")
