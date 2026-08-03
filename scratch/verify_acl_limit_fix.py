import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core_engine import fetch_acl_papers

print("--- Testing ACL Paper Fetch with Custom Limits ---")

p10 = fetch_acl_papers(max_results=10, search_query="reasoning", volume_filter="all")
print(f"Requested max_results=10 -> Fetched: {len(p10)} papers")

p25 = fetch_acl_papers(max_results=25, search_query="reasoning", volume_filter="all")
print(f"Requested max_results=25 -> Fetched: {len(p25)} papers")

p50 = fetch_acl_papers(max_results=50, search_query="reasoning", volume_filter="all")
print(f"Requested max_results=50 -> Fetched: {len(p50)} papers")

assert len(p10) == 10
assert len(p25) == 25
assert len(p50) == 50
print("✅ Verification successful! fetch_acl_papers respects custom max_results limits.")
