"""Generate `static/sitemap.xml` from `dashboard.beaches_data.BEACHES`.

Run locally or in CI before building the image so the sitemap includes all
beach permalinks and their lastmod timestamps.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.parse import quote_plus
import unicodedata


def slugify(name: str) -> str:
    # Normalize, remove diacritics, keep ascii, lower, replace non-alnum with '-'
    n = unicodedata.normalize('NFKD', name)
    n = n.encode('ascii', 'ignore').decode('ascii')
    n = n.lower()
    # replace spaces and non-alphanum with '-'
    out = []
    for ch in n:
        if ch.isalnum():
            out.append(ch)
        else:
            out.append('-')
    s = ''.join(out)
    # collapse consecutive dashes
    while '--' in s:
        s = s.replace('--', '-')
    s = s.strip('-')
    return s

import sys
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
from dashboard.beaches_data import BEACHES

BASE = "https://descubreplayas.com.do"
OUT = Path(__file__).resolve().parents[1] / "static" / "sitemap.xml"


def make_url(name: str) -> str:
    slug = slugify(name)
    return f"{BASE}/beach/{slug}"


def generate() -> None:
    today = date.today().isoformat()
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]

    # Main page
    parts.append("  <url>")
    parts.append(f"    <loc>{BASE}/</loc>")
    parts.append(f"    <lastmod>{today}</lastmod>")
    parts.append("    <changefreq>daily</changefreq>")
    parts.append("    <priority>1.0</priority>")
    parts.append("  </url>")

    # Beaches: stable ordering using the list order
    for b in BEACHES:
        name = b.get("name")
        if not name:
            continue
        loc = make_url(name)
        parts.append("  <url>")
        parts.append(f"    <loc>{loc}</loc>")
        parts.append(f"    <lastmod>{today}</lastmod>")
        parts.append("    <changefreq>weekly</changefreq>")
        parts.append("    <priority>0.6</priority>")
        parts.append("  </url>")

    parts.append("</urlset>")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"Wrote sitemap to {OUT} with {len(BEACHES)} beaches")


if __name__ == "__main__":
    generate()
