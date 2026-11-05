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
import datetime as dt
import io
import logging
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
from scripts._commons import Candidate, ManualCheck, fetch_nearby, parse_page  # noqa: E402
from scripts._http import Api, FetchError, RateLimited, make_client  # noqa: E402
from scripts.fetch_species import SKIP_BEACHES, write_atomic  # noqa: E402

logger = logging.getLogger(__name__)

MAX_CANDIDATES_PER_BEACH = 10
# Two beaches in a row answered 429: more requests won't help, so stop being a nuisance.
MAX_CONSECUTIVE_RATE_LIMITS = 2
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "info" / "photo_candidates.md"
_MARKDOWN_SPECIALS = str.maketrans({c: f"\\{c}" for c in "\\`*_[]|<>#"})
_ERROR_RATE_LIMITED = "HTTP 429: wrong size or rate limited"
_ERROR_NOT_ATTEMPTED = "not attempted: stopped after repeated HTTP 429"


@dataclass
class BeachReport:
    """What one beach's search found."""

    name: str
    candidates: list[Candidate] = field(default_factory=list)
    manual: list[ManualCheck] = field(default_factory=list)
    refused: Counter = field(default_factory=Counter)
    error: str | None = None


def build_report(name: str, pages: Iterable[object], today: dt.date) -> BeachReport:
    """Sort a beach's pages into candidates, manual checks and refusals."""
    report = BeachReport(name)
    for page in pages:
        result = parse_page(page, today)
        if isinstance(result, Candidate):
            report.candidates.append(result)
        elif isinstance(result, ManualCheck):
            report.manual.append(result)
        else:
            report.refused[result.reason] += 1
    report.refused["over the per-beach list limit"] += max(0, len(report.candidates) - MAX_CANDIDATES_PER_BEACH)
    report.candidates = report.candidates[:MAX_CANDIDATES_PER_BEACH]
    return report


def collect(api: Api, beaches: Iterable[Mapping], today: dt.date) -> list[BeachReport]:
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
            reports.append(build_report(name, fetch_nearby(api, beach["latitude"], beach["longitude"]), today))
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


def _write_beach(out: TextIO, report: BeachReport) -> None:
    out.write(f"\n## {_md(report.name)}\n\n")
    if report.error:
        out.write(f"**FAILED:** {_md(report.error)}\n")
        return
    if report.candidates:
        out.write("| File | Licence | Author | Year | Size |\n|---|---|---|---|---|\n")
        for item in report.candidates:
            year = item.year if item.year is not None else "—"
            out.write(
                f"| {_link(item.title, item.source_url)} | {_link(item.license.label, item.license.url)} "
                f"| {_md(item.author)} | {year} | {item.width}×{item.height} |\n"
            )
    else:
        out.write("No candidates.\n")
    if report.manual:
        out.write("\nNeeds manual check (the licence name and URL disagree; refused):\n\n")
        for item in report.manual:
            out.write(f"- {_link(item.title, item.source_url)}: {_md(item.license_name or 'no name')} / "
                      f"{_md(item.license_url or 'no URL')}\n")
    if report.refused:
        reasons = ", ".join(f"{reason}: {count}" for reason, count in sorted(report.refused.items()))
        out.write(f"\nRefused automatically: {_md(reasons)}\n")


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


def format_summary(reports: Iterable[BeachReport]) -> str:
    """One line per beach, then totals, so a failure can't go unnoticed."""
    reports = list(reports)
    lines = [f"{'beach':<32}{'cand':>5}{'manual':>7}{'refused':>8}"]
    totals: Counter = Counter()
    for report in reports:
        refused = sum(report.refused.values())
        lines.append(f"{report.name:<32}{len(report.candidates):>5}{len(report.manual):>7}{refused:>8}")
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
    args = parser.parse_args(argv)
    by_name = {beach["name"]: beach for beach in BEACHES}
    unknown = [name for name in args.beach or [] if name not in by_name]
    if unknown:
        raise SystemExit(f"Unknown beach: {', '.join(unknown)}")
    selected = [by_name[name] for name in args.beach] if args.beach else list(BEACHES)
    beaches = [beach for beach in selected if beach["name"] not in SKIP_BEACHES]
    skipped = [beach["name"] for beach in selected if beach["name"] in SKIP_BEACHES]
    with make_client(transport) as client:
        reports = collect(Api(client, sleep), beaches, dt.date.today())
    print(format_summary(reports))
    buffer = io.StringIO()
    format_markdown(reports, skipped, buffer)
    write_atomic(args.output, buffer.getvalue())
    print(f"Wrote {args.output}")
    return 1 if any(report.error for report in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
