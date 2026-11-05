# Copyright 2026 Ayesha Yege
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""List Wikimedia Commons photos near each beach, for a person to review.

A research aid. It writes info/photo_candidates.md and never touches the data files.
Run it with the ingest environment:

    .venv-ingest/bin/python scripts/beach_photo_candidates.py --beach "Playa Bávaro"
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import html
import io
import logging
import shlex
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.beaches_data import BEACHES  # noqa: E402  (sys.path set above)
from scripts._beach_match import MIXED, TAGGED, NameRule, beach_name_rules, classify  # noqa: E402
from dashboard.conoce_data import MAX_ALT_CHARS  # noqa: E402
from scripts._commons import (  # noqa: E402
    DOWNLOAD_HOST,
    MAX_SEARCH_RADIUS_M,
    MIN_SEARCH_RADIUS_M,
    SEARCH_RADIUS_M,
    Candidate,
    ManualCheck,
    fetch_nearby,
    parse_page,
)
from scripts._http import Api, FetchError, RateLimited, make_client  # noqa: E402
from scripts.fetch_species import SKIP_BEACHES, write_atomic  # noqa: E402

logger = logging.getLogger(__name__)

MAX_CANDIDATES_PER_BEACH = 10  # per group
REASON_OVER_LIMIT = "over the per-beach list limit"
IMPORT_SCRIPT = ".venv-ingest/bin/python scripts/import_beach_photo.py"
# Two beaches in a row answered 429: more requests won't help, so stop being a nuisance.
MAX_CONSECUTIVE_RATE_LIMITS = 2
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "info" / "photo_candidates.md"
CONTACT_SHEET_IMAGE_WIDTH_PX = 250
MANIFEST_PATH_HINT = "info/photo_picks.json"
REVIEW_COMMAND = ".venv-ingest/bin/python scripts/review_photos.py"
# The page's one script is allowed by its hash, and nothing else may run or load.
_SHEET_SCRIPT = """\
(function () {
  "use strict";
  var STORE = "photoPicks.v1";
  var picks = Array.prototype.slice.call(document.querySelectorAll(".pick"));
  var out = document.getElementById("manifest");
  var status = document.getElementById("status");
  var result = document.getElementById("result");
  var submit = document.getElementById("submit");
  function key(pick) { return pick.dataset.beach + "|" + pick.dataset.file; }
  function card(pick) { return pick.closest(".card"); }
  function alt(pick, lang) { return card(pick).querySelector(".alt-" + lang); }
  var saved = {};
  try { saved = JSON.parse(localStorage.getItem(STORE)) || {}; } catch (e) { saved = {}; }
  picks.forEach(function (pick) {
    var s = saved[key(pick)];
    if (!s) { return; }
    pick.checked = !!s.on;
    alt(pick, "es").value = s.es || "";
    alt(pick, "en").value = s.en || "";
  });
  function update() {
    var items = [], withAlt = 0, half = 0, snapshot = {};
    picks.forEach(function (pick) {
      var es = alt(pick, "es").value.trim(), en = alt(pick, "en").value.trim();
      if (pick.checked || es || en) { snapshot[key(pick)] = { on: pick.checked, es: es, en: en }; }
      var c = card(pick);
      c.classList.toggle("picked", pick.checked);
      c.classList.remove("incomplete");
      if (!pick.checked) { return; }
      var item = { beach: pick.dataset.beach, commons_file: pick.dataset.file };
      if (es && en) { item.alt_es = es; item.alt_en = en; withAlt += 1; }
      else if (es || en) { half += 1; c.classList.add("incomplete"); return; }
      items.push(item);
    });
    try { localStorage.setItem(STORE, JSON.stringify(snapshot)); } catch (e) { /* private mode */ }
    out.value = JSON.stringify({ photos: items }, null, 2);
    status.textContent = items.length + " picked, " + withAlt + " with alt text" + (half ? ", " + half + " with only one alt text" : "");
    submit.disabled = items.length === 0 || half > 0;
  }
  picks.forEach(function (pick) {
    pick.addEventListener("change", function () {
      if (pick.checked) {
        picks.forEach(function (other) {
          if (other !== pick && other.dataset.beach === pick.dataset.beach) { other.checked = false; }
        });
      }
      result.textContent = "";
      update();
    });
  });
  document.addEventListener("input", function () { result.textContent = ""; update(); });
  document.getElementById("copy").addEventListener("click", function () {
    out.select();
    try { document.execCommand("copy"); } catch (e) { /* the text is selected anyway */ }
  });
  submit.addEventListener("click", function () {
    submit.disabled = true;
    result.textContent = "Saving...";
    fetch("submit", { method: "POST", headers: { "Content-Type": "application/json" }, body: out.value })
      .then(function (response) {
        return response.json().then(function (body) { return { ok: response.ok, body: body }; });
      })
      .then(function (r) {
        result.textContent = r.ok
          ? "Saved " + r.body.saved + " picks to " + r.body.path + ". Now tell Claude."
          : "Not saved: " + (r.body.error || "error");
      })
      .catch(function () {
        result.textContent = "Could not reach the review server. Open this page with scripts/review_photos.py, or copy the manifest instead.";
      })
      .then(update);
  });
  update();
})();
"""


def _sheet_csp() -> str:
    digest = base64.b64encode(hashlib.sha256(_SHEET_SCRIPT.encode("utf-8")).digest()).decode("ascii")
    return (
        f"default-src 'none'; img-src https://{DOWNLOAD_HOST}; style-src 'unsafe-inline'; "
        f"script-src 'sha256-{digest}'; connect-src 'self'; base-uri 'none'; form-action 'none'"
    )


# The angle brackets make the importer refuse these until a person replaces them.
_ALT_PLACEHOLDER_ES = "<describe la escena>"
_ALT_PLACEHOLDER_EN = "<describe the scene>"
MAX_CATEGORIES_SHOWN = 4
_MARKDOWN_SPECIALS = str.maketrans({c: f"\\{c}" for c in "\\`*_[]|<>#"})
_ERROR_RATE_LIMITED = "HTTP 429: wrong size or rate limited"
_ERROR_NOT_ATTEMPTED = "not attempted: stopped after repeated HTTP 429"


@dataclass
class BeachReport:
    """What one beach's search found. A file that passed the automatic checks is in
    exactly one of tagged, mixed, unknown, or counted as dropped.
    """

    name: str
    nearby: int = 0
    tagged: list[Candidate] = field(default_factory=list)
    mixed: list[Candidate] = field(default_factory=list)
    unknown: list[Candidate] = field(default_factory=list)
    dropped: int = 0
    manual: list[ManualCheck] = field(default_factory=list)
    refused: Counter = field(default_factory=Counter)
    error: str | None = None

    @property
    def candidates(self) -> list[Candidate]:
        """Everything listed for review, in the order it is shown."""
        return [*self.tagged, *self.mixed, *self.unknown]


def build_report(
    name: str,
    pages: Iterable[object],
    today: dt.date,
    categories_complete: bool = True,
    rule: NameRule | None = None,
) -> BeachReport:
    """Sort a beach's pages by the automatic checks, then by their categories. When
    the categories are incomplete, a file with no beach category yet is "unknown"
    rather than dropped, because the missing ones might have been the match.
    """
    report = BeachReport(name)
    for page in pages:
        report.nearby += 1
        result = parse_page(page, today)
        if isinstance(result, ManualCheck):
            report.manual.append(result)
        elif not isinstance(result, Candidate):
            report.refused[result.reason] += 1
        else:
            kind = classify(result.categories, rule)
            if kind == TAGGED:
                report.tagged.append(result)
            elif kind == MIXED:
                report.mixed.append(result)
            elif categories_complete:
                report.dropped += 1
            else:
                report.unknown.append(result)
    for group in (report.tagged, report.mixed, report.unknown):
        report.refused[REASON_OVER_LIMIT] += max(0, len(group) - MAX_CANDIDATES_PER_BEACH)
        del group[MAX_CANDIDATES_PER_BEACH:]
    return report


def collect(
    api: Api,
    beaches: Iterable[Mapping],
    today: dt.date,
    radius_m: int = SEARCH_RADIUS_M,
    rules: Mapping[str, NameRule] | None = None,
) -> list[BeachReport]:
    """Search one beach at a time. A failure stops that beach, never the whole run,
    unless the server keeps saying 429.
    """
    reports: list[BeachReport] = []
    rate_limited_in_a_row = 0
    for beach in beaches:
        name = beach["name"]
        if rate_limited_in_a_row >= MAX_CONSECUTIVE_RATE_LIMITS:
            reports.append(BeachReport(name, error=_ERROR_NOT_ATTEMPTED))
            continue
        try:
            nearby = fetch_nearby(api, beach["latitude"], beach["longitude"], radius_m)
            rule = rules.get(name) if rules else None
            reports.append(build_report(name, nearby.pages, today, nearby.categories_complete, rule))
            rate_limited_in_a_row = 0
        except RateLimited:
            rate_limited_in_a_row += 1
            logger.warning("beach_photo_candidates: %s: %s", name, _ERROR_RATE_LIMITED)
            reports.append(BeachReport(name, error=_ERROR_RATE_LIMITED))
        except (FetchError, httpx.HTTPError) as exc:
            logger.warning("beach_photo_candidates: %s failed: %s", name, exc)
            reports.append(BeachReport(name, error=type(exc).__name__))
    return reports


def _md(text: object) -> str:
    """Escape untrusted text for a Markdown table cell."""
    return " ".join(str(text).split()).translate(_MARKDOWN_SPECIALS)


def _link(label: str, url: str) -> str:
    return f"[{_md(label)}](<{url}>)"


def _group_table(out: TextIO, title: str, items: list[Candidate]) -> None:
    if not items:
        return
    out.write(f"\n{title}\n\n| File | Licence | Author | Year | Size | Categories |\n|---|---|---|---|---|---|\n")
    for item in items:
        year = item.year if item.year is not None else "—"
        categories = ", ".join(item.categories[:MAX_CATEGORIES_SHOWN]) or "—"
        out.write(
            f"| {_link(item.title, item.source_url)} | {_link(item.license.label, item.license.url)} "
            f"| {_md(item.author)} | {year} | {item.width}×{item.height} | {_md(categories)} |\n"
        )


def _write_beach(out: TextIO, report: BeachReport) -> None:
    out.write(f"\n## {_md(report.name)}\n\n")
    if report.error:
        out.write(f"**FAILED:** {_md(report.error)}\n")
        return
    out.write(counts_line(report) + "\n")
    _group_table(out, "**Beach-tagged**", report.tagged)
    _group_table(out, "**Beach-tagged but mixed** (a category also suggests a hotel, pool, market or the like)", report.mixed)
    _group_table(out, "**Categories unknown** (the category list was incomplete; not checked)", report.unknown)
    if report.manual:
        out.write("\nNeeds manual check (the licence name and URL disagree; refused):\n\n")
        for item in report.manual:
            out.write(f"- {_link(item.title, item.source_url)}: {_md(item.license_name or 'no name')} / "
                      f"{_md(item.license_url or 'no URL')}\n")
    if report.refused:
        reasons = ", ".join(f"{reason}: {count}" for reason, count in sorted(report.refused.items()))
        out.write(f"\nRefused automatically: {_md(reasons)}\n")


def counts_line(report: BeachReport) -> str:
    """nearby / beach-tagged / mixed / categories unknown / dropped, for one beach."""
    return (
        f"Nearby {report.nearby} · beach-tagged {len(report.tagged)} · mixed {len(report.mixed)} · "
        f"categories unknown {len(report.unknown)} · dropped {report.dropped}"
    )


def format_markdown(reports: Iterable[BeachReport], skipped: Iterable[str], out: TextIO) -> None:
    """Write the review file. Nothing here has been checked by a person yet."""
    out.write(
        "# Beach photo candidates\n\n"
        "A research aid. Check each photo for identifiable people and children, and that "
        "the beach is recognisable. `import_beach_photo.py` fetches the licence again.\n"
    )
    for report in reports:
        _write_beach(out, report)
    for name in skipped:
        out.write(f"\n## {_md(name)}\n\nSkipped: {_md(SKIP_BEACHES[name])}\n")


def import_command(beach: str, title: str) -> str:
    """The shell command that imports one candidate. Both values are quoted, and the
    alt text is a placeholder the importer refuses, so a person has to write it.
    """
    return (
        f"{IMPORT_SCRIPT} --beach {shlex.quote(beach)} --commons-file {shlex.quote(title)} "
        f'--alt-es "{_ALT_PLACEHOLDER_ES}" --alt-en "{_ALT_PLACEHOLDER_EN}"'
    )


def _sheet_card(beach: str, number: int, item: Candidate) -> str:
    e = html.escape
    year = item.year if item.year is not None else "—"
    return (
        '<figure class="card">'
        f'<img src="{e(item.thumbnail_url)}" width="{CONTACT_SHEET_IMAGE_WIDTH_PX}" loading="lazy" alt="">'
        f'<figcaption><label class="pickrow"><input type="checkbox" class="pick" data-beach="{e(beach)}" '
        f'data-file="{e(item.title)}"> <b>#{number}</b> pick this one</label><br>'
        f'<a href="{e(item.source_url)}" rel="noopener noreferrer">{e(item.title)}</a><br>'
        f"{e(item.license.label)} · {e(item.author)} · {year} · {item.width}×{item.height}"
        f"<br><small>{e(', '.join(item.categories[:MAX_CATEGORIES_SHOWN]))}</small>"
        f'<input class="alt-es" maxlength="{MAX_ALT_CHARS}" placeholder="Alt text, español (optional)" aria-label="Alt text in Spanish, optional">'
        f'<input class="alt-en" maxlength="{MAX_ALT_CHARS}" placeholder="Alt text, English (optional)" aria-label="Alt text in English, optional">'
        f"<details><summary>one-photo command</summary><code>{e(import_command(beach, item.title))}</code></details>"
        "</figcaption></figure>"
    )


_SHEET_CSS = (
    ":root{color-scheme:light dark}body{font:14px system-ui;margin:16px 16px 190px;max-width:1100px}"
    ".grid{display:flex;flex-wrap:wrap;gap:12px}.card{margin:0;width:260px;padding:6px;border:2px solid transparent;border-radius:8px}"
    ".card img{display:block;max-width:100%;height:auto}.card.picked{border-color:#2e7d32}.card.incomplete{border-color:#c62828}"
    ".card input[type=text],.card input:not([type]){display:block;width:100%;box-sizing:border-box;margin-top:4px}"
    "code{display:block;margin-top:6px;font-size:11px;word-break:break-all;user-select:all}"
    ".pickrow{font-weight:600;cursor:pointer}nav{columns:3 220px;font-size:13px}"
    "#bar{position:fixed;left:0;right:0;bottom:0;padding:8px 16px;background:Canvas;border-top:1px solid GrayText}"
    "#manifest{width:100%;box-sizing:border-box;height:90px;font:11px monospace}"
)


def format_contact_sheet(reports: Iterable[BeachReport], skipped: Iterable[str], out: TextIO) -> None:
    """One page of thumbnails per beach, for choosing a photo by eye. Pick one per
    beach, type its alt text, and copy the manifest at the bottom. Every value is
    escaped, and a CSP meta tag lets the page run only its own script and load only
    Commons thumbnails.
    """
    e = html.escape
    reports = list(reports)
    out.write(
        f'<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="{e(_sheet_csp())}">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>Beach photo candidates</title>'
        f"<style>{_SHEET_CSS}</style><h1>Beach photo candidates</h1>"
        "<p>Check each photo for identifiable people and children, and that the beach is recognisable. Open the title "
        "for the full photo. Tick one photo per beach, then press Submit. Alt text is optional: leave it empty and "
        "Claude writes it for you to approve. Your picks are kept if you reload.</p><nav>"
    )
    with_photos = [(index, report) for index, report in enumerate(reports) if report.candidates]
    out.writelines(f'<a href="#beach-{index}">{e(report.name)} ({len(report.candidates)})</a><br>' for index, report in with_photos)
    out.write("</nav>")
    for index, report in enumerate(reports):
        number = 0
        out.write(f'<h2 id="beach-{index}">{e(report.name)}</h2>')
        if report.error:
            out.write(f"<p><b>FAILED:</b> {e(report.error)}</p>")
            continue
        out.write(f"<p>{e(counts_line(report))}</p>")
        for heading, group in (("Beach-tagged", report.tagged), ("Beach-tagged but mixed", report.mixed),
                               ("Categories unknown", report.unknown)):
            if group:
                out.write(f'<h3>{e(heading)}</h3><div class="grid">')
                for item in group:
                    number += 1
                    out.write(_sheet_card(report.name, number, item))
                out.write("</div>")
    for name in skipped:
        out.write(f"<h2>{e(name)}</h2><p>Skipped: {e(SKIP_BEACHES[name])}</p>")
    out.write(
        '<div id="bar"><b id="status"></b> <button id="submit" type="button" disabled>Submit picks</button> '
        '<span id="result" role="status"></span> <button id="copy" type="button">Copy manifest instead</button> '
        f"<small>Submit works when this page is opened with <code style=\"display:inline\">{e(REVIEW_COMMAND)}</code></small>"
        '<textarea id="manifest" readonly aria-label="Manifest to copy"></textarea></div>'
        f"<script>{_SHEET_SCRIPT}</script>"
    )


def format_summary(reports: Iterable[BeachReport]) -> str:
    """One line per beach, then totals, so a failure can't go unnoticed."""
    reports = list(reports)
    lines = [f"{'beach':<32}{'near':>5}{'tag':>5}{'mixed':>6}{'unkn':>5}{'drop':>5}{'other':>6}"]
    totals: Counter = Counter()
    for report in reports:
        other = sum(report.refused.values()) - report.refused[REASON_OVER_LIMIT]
        lines.append(
            f"{report.name:<32}{report.nearby:>5}{len(report.tagged):>5}{len(report.mixed):>6}"
            f"{len(report.unknown):>5}{report.dropped:>5}{other + len(report.manual):>6}"
        )
        totals.update(report.refused)
    lines.extend(f"REFUSED {reason}: {count}" for reason, count in sorted(totals.items()))
    lines.extend(f"FAILED {report.name}: {report.error}" for report in reports if report.error)
    return "\n".join(lines)


def main(
    argv: list[str] | None = None,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--beach", action="append", help="search only this beach (repeatable)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Markdown file to write")
    parser.add_argument("--html", type=Path, help="also write a contact sheet with thumbnails here")
    parser.add_argument(
        "--radius", type=int, default=SEARCH_RADIUS_M, choices=range(MIN_SEARCH_RADIUS_M, MAX_SEARCH_RADIUS_M + 1),
        metavar=f"{MIN_SEARCH_RADIUS_M}-{MAX_SEARCH_RADIUS_M}", help="search radius in metres",
    )
    args = parser.parse_args(argv)
    by_name = {beach["name"]: beach for beach in BEACHES}
    unknown = [name for name in args.beach or [] if name not in by_name]
    if unknown:
        raise SystemExit(f"Unknown beach: {', '.join(unknown)}")
    rules = beach_name_rules(BEACHES)
    selected = [by_name[name] for name in args.beach] if args.beach else list(BEACHES)
    beaches = [beach for beach in selected if beach["name"] not in SKIP_BEACHES]
    skipped = [beach["name"] for beach in selected if beach["name"] in SKIP_BEACHES]
    with make_client(transport) as client:
        reports = collect(Api(client, sleep), beaches, dt.date.today(), args.radius, rules)
    print(format_summary(reports))
    buffer = io.StringIO()
    format_markdown(reports, skipped, buffer)
    write_atomic(args.output, buffer.getvalue())
    print(f"Wrote {args.output}")
    if args.html:
        sheet = io.StringIO()
        format_contact_sheet(reports, skipped, sheet)
        write_atomic(args.html, sheet.getvalue())
        print(f"Wrote {args.html}")
    return 1 if any(report.error for report in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
