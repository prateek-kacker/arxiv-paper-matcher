import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

index_html = Path("static/index.html").read_text(encoding="utf-8")
app_js = Path("static/app.js").read_text(encoding="utf-8")

# 1. Radio Names
js_radios = set(re.findall(r'input\[name=["\']([^"\']+)["\']\]', app_js))
html_radios = set(re.findall(r'<input[^>]*type=["\']radio["\'][^>]*name=["\']([^"\']+)["\']', index_html))
html_radios.update(re.findall(r'<input[^>]*name=["\']([^"\']+)["\'][^>]*type=["\']radio["\']', index_html))

print("=== Radio Button Names Audit ===")
print(f"JS Radio target names: {sorted(js_radios)}")
print(f"HTML Radio names:     {sorted(html_radios)}")

missing_radios = js_radios - html_radios
if missing_radios:
    print(f"⚠️ Missing Radio names in HTML: {missing_radios}")
else:
    print("✅ All Radio button names match!")

# 2. Main Tab IDs
js_tabs = set(re.findall(r'data-tab=["\']([^"\']+)["\']', app_js))
html_tabs = set(re.findall(r'data-tab=["\']([^"\']+)["\']', index_html))
html_section_ids = set(re.findall(r'<section[^>]*id=["\']([^"\']+)["\']', index_html))

print("\n=== Main Tabs Audit ===")
print(f"HTML Tab buttons (data-tab): {sorted(html_tabs)}")
print(f"HTML Tab section IDs:       {sorted(html_section_ids)}")

missing_tabs = html_tabs - html_section_ids
if missing_tabs:
    print(f"⚠️ Warning: data-tab values without matching <section id>: {missing_tabs}")
else:
    print("✅ All data-tab targets match section IDs!")
