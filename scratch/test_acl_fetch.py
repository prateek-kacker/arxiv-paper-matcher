import urllib.request
import re

url = 'https://aclanthology.org/2024.acl-long.1/'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
try:
    html = urllib.request.urlopen(req).read().decode('utf-8')
    m = re.search(r'class="card-body acl-abstract"[^>]*>(.*?)</div>', html, re.DOTALL)
    if m:
        abstract_text = re.sub(r'<[^>]+>', '', m.group(1)).replace('Abstract', '', 1).strip()
        print("EXACT ABSTRACT:\n", abstract_text)
except Exception as e:
    print('Error:', e)
