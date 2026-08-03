import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

server_py = Path("server.py").read_text(encoding="utf-8")
app_js = Path("static/app.js").read_text(encoding="utf-8")

# Extract route paths from server.py (e.g., @app.get("/api/..."))
backend_routes = set(re.findall(r'@app\.(?:get|post|put|delete)\(["\'](/api/[^"\'?]+)["\']', server_py))

# Extract fetch calls in app.js (e.g., fetch('/api/...'))
js_fetches = set(re.findall(r'fetch\(["\']`?(/api/[^"\'?`]+)`?["\']', app_js))

# Also extract template string fetches like fetch(`/api/schedules/${id}`) -> /api/schedules/
js_template_fetches = set(re.findall(r'fetch\(`(/api/[^`]+)`\)', app_js))

print("=== Backend API Routes in server.py ===")
for r in sorted(backend_routes):
    print(f"  - {r}")

print("\n=== Frontend Fetch Calls in static/app.js ===")
for f in sorted(js_fetches.union(js_template_fetches)):
    print(f"  - {f}")

# Clean template strings for matching
matched_count = 0
unmatched = []

for js_f in js_fetches.union(js_template_fetches):
    # Replace ${...} or numbers with placeholders
    pattern_js = re.sub(r'\$\{[^}]+\}', '[PARAM]', js_f)
    pattern_js = re.sub(r'/\d+', '/[PARAM]', pattern_js)
    
    found = False
    for br in backend_routes:
        pattern_br = re.sub(r'\{[^}]+\}', '[PARAM]', br)
        if pattern_js == pattern_br or js_f.startswith(br.split('{')[0]):
            found = True
            break
    if found:
        matched_count += 1
    else:
        unmatched.append(js_f)

print(f"\nAudit Summary:")
print(f"Total frontend endpoints: {len(js_fetches.union(js_template_fetches))}")
print(f"Successfully matched: {matched_count}")
if unmatched:
    print(f"⚠️ Unmatched frontend fetch calls: {unmatched}")
else:
    print("✅ All frontend fetch endpoints match backend API routes!")
