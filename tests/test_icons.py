"""Offline checks for dashboard.icons and for the app's no-emoji rule.

Run: python -m tests.test_icons
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from dashboard.conoce import DEFAULT_GROUP_ICON, ICONIC_GROUP_ICON, TITLE_ICON
from dashboard.icons import ICON_NAMES, icon

_DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard"
# The explorer's files. app.py is the older coastal map and is not covered.
_APP_FILES = ("beaches.py", "conoce.py", "surf.py", "risk_overlay.py", "map_perf.py")
_ICON_CALL = re.compile(r"""\bicon\(\s*["']([\w-]+)["']""")
# U+2715 (the close-button glyph) is plain text, not an emoji.
_EMOJI = re.compile(r"(?!✕)[\U0001F000-\U0001FAFF☀-➿⭐ℹ⏰-⏿]")
# Page metadata outside the visible UI: the browser-tab icon and the share title.
_EMOJI_ALLOWED = ("page_icon=", "_OG_TITLE =")


def _check(condition: bool, message: str) -> None:
    assert condition, message


def test_every_icon_used_exists() -> None:
    for name in _APP_FILES:
        for used in _ICON_CALL.findall((_DASHBOARD / name).read_text(encoding="utf-8")):
            _check(used in ICON_NAMES, f"{name}: icon {used!r} is not in dashboard.icons")
    for used in (*ICONIC_GROUP_ICON.values(), DEFAULT_GROUP_ICON, TITLE_ICON):
        _check(used in ICON_NAMES, f"conoce: icon {used!r} is not in dashboard.icons")


def test_no_emoji_in_the_ui() -> None:
    for name in _APP_FILES:
        for number, line in enumerate((_DASHBOARD / name).read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#") or any(a in line for a in _EMOJI_ALLOWED):
                continue
            _check(not _EMOJI.search(line), f"{name}:{number} has an emoji; use dashboard.icons")


def test_icon_markup() -> None:
    plain = icon("waves")
    _check(plain.startswith('<svg class="ic ic-waves"') and plain.endswith("</svg>"), "an inline svg")
    _check('aria-hidden="true"' in plain and "<title>" not in plain, "decorative by default")
    named = icon("turtle", label='Área "protegida"')
    _check('role="img"' in named and "<title>Área &quot;protegida&quot;</title>" in named, "a label is escaped")


def main() -> int:
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    for name, test in tests:
        test()
        print(f"  ok {name}")
    print(f"All {len(tests)} icon tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
