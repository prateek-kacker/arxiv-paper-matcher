"""
Automated validation tests for Frontend assets (static/app.js, static/index.html, static/styles.css)
"""

import re
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


def test_js_syntax_check():
    """Run node --check on static/app.js to ensure zero JavaScript syntax errors."""
    app_js_path = PROJECT_ROOT / "static" / "app.js"
    res = subprocess.run(["node", "--check", str(app_js_path)], capture_output=True, text=True)
    assert res.returncode == 0, f"JavaScript syntax error in app.js:\n{res.stderr}"


def test_dom_ids_exist_in_html():
    """Verify that every getElementById call in app.js references an ID present in index.html."""
    index_html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    html_ids = set(re.findall(r'id=["\']([^"\']+)["\']', index_html))
    js_ids = set(re.findall(r'getElementById\(["\']([^"\']+)["\']\)', app_js))

    missing = js_ids - html_ids
    assert not missing, f"IDs referenced in app.js but missing in index.html: {missing}"


def test_tab_navigation_mappings():
    """Verify that all data-tab attributes match existing section panel IDs in index.html."""
    index_html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    data_tabs = set(re.findall(r'data-tab=["\']([^"\']+)["\']', index_html))
    section_ids = set(re.findall(r'<section[^>]*id=["\']([^"\']+)["\']', index_html))

    missing_sections = data_tabs - section_ids
    assert not missing_sections, f"data-tab attributes missing corresponding <section id>: {missing_sections}"


def test_css_assets_exist():
    """Verify that styles.css exists and contains valid theme definitions."""
    css_path = PROJECT_ROOT / "static" / "styles.css"
    assert css_path.exists()
    content = css_path.read_text(encoding="utf-8")
    assert "--color-accent" in content
    assert ".tabs-header" in content
