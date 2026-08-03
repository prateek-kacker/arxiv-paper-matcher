"""
Comprehensive button wiring tests for static frontend assets.
These tests guard against silent regressions where UI buttons stop persisting state.
"""

from html.parser import HTMLParser
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).parent.parent
INDEX_HTML = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
APP_JS = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")


class _ButtonParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.form_stack = []
        self.buttons = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "form":
            self.form_stack.append(attrs_dict.get("id"))
            return
        if tag != "button":
            return

        cls = attrs_dict.get("class", "")
        class_list = [c for c in cls.split() if c]
        self.buttons.append(
            {
                "id": attrs_dict.get("id"),
                "type": attrs_dict.get("type", "submit"),
                "classes": class_list,
                "data_tab": attrs_dict.get("data-tab"),
                "data_subtab": attrs_dict.get("data-subtab"),
                "form_id": self.form_stack[-1] if self.form_stack else None,
                "raw": self.get_starttag_text(),
            }
        )

    def handle_endtag(self, tag):
        if tag == "form" and self.form_stack:
            self.form_stack.pop()


def _parse_buttons():
    parser = _ButtonParser()
    parser.feed(INDEX_HTML)
    return parser.buttons


def test_every_index_button_is_wired_to_js_handler():
    """Every button in index.html must be connected to JS logic (directly or delegated)."""
    buttons = _parse_buttons()
    missing = []

    has_tab_delegate = "tab-btn" in APP_JS and "navigateToTab" in APP_JS
    has_subtab_delegate = "subtab-btn" in APP_JS and "data-subtab" in APP_JS

    for b in buttons:
        btn_id = b["id"]
        classes = set(b["classes"])
        form_id = b["form_id"]

        if btn_id and btn_id in APP_JS:
            continue
        if "tab-btn" in classes and has_tab_delegate:
            continue
        if "subtab-btn" in classes and has_subtab_delegate:
            continue

        # Submit buttons without IDs are wired by form submit handlers.
        if b["type"] == "submit" and form_id in {"form-schedule", "form-edit-schedule"}:
            if f"getElementById('{form_id}')" in APP_JS or f'getElementById("{form_id}")' in APP_JS:
                continue

        missing.append(b["raw"])

    assert not missing, "Buttons missing JS wiring:\n" + "\n".join(missing)


def test_all_dynamic_button_classes_have_event_bindings():
    """Buttons rendered from JS templates must have matching event listeners."""
    class_tokens = set()
    for class_attr in re.findall(r'class="([^"]+)"', APP_JS):
        for token in class_attr.split():
            if token.startswith("btn-") and token not in {"btn-primary", "btn-secondary"}:
                class_tokens.add(token)

    listener_classes = set(re.findall(r"querySelectorAll\('\.(btn-[a-z0-9-]+)'\)", APP_JS))
    delegated_classes = set(re.findall(r"classList\.contains\('([a-z0-9-]+)'\)", APP_JS))

    allowed_delegated = {"tab-btn", "subtab-btn"}
    # btn-ghost is used for anchor links (no JS click handler required).
    ignore_classes = {"btn-ghost"}

    missing = sorted(
        c for c in class_tokens
        if c not in listener_classes and c not in delegated_classes and c not in allowed_delegated and c not in ignore_classes
    )

    assert not missing, f"Dynamic button classes without event binding: {missing}"


def test_recurring_schedule_buttons_use_persistent_api_contracts():
    """Recurring schedule actions must call persistence APIs and validate response success."""
    required_api_calls = [
        "/api/schedules",                 # create
        "/api/schedules/${id}",           # edit/delete
        "/api/schedules/${id}/toggle",    # activate/pause
        "/api/schedules/${id}/run",       # run now
    ]
    for call in required_api_calls:
        assert call in APP_JS, f"Missing schedule API path in app.js: {call}"

    # Create / edit / run / toggle / delete handlers should verify API outcome.
    assert APP_JS.count("!res.ok || !data.success") >= 5, (
        "Expected response validation checks for recurring schedule button actions."
    )


def test_all_btn_ids_are_referenced_in_js():
    """All explicit btn-* IDs in HTML should be referenced in app.js."""
    ids = set(re.findall(r'id="(btn-[^"]+)"', INDEX_HTML))
    missing = sorted(i for i in ids if i not in APP_JS)
    assert not missing, f"Button IDs not referenced in app.js: {missing}"
