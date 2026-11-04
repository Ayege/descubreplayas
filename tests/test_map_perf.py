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

"""Offline tests for the helpers that keep the map and card fast.

Run from the repo root:

    python -m tests.test_map_perf
"""
from __future__ import annotations

import ast
import logging
import sys
import time
from pathlib import Path

import folium

from dashboard.map_perf import CompactCircles, beach_from_map_click, run_parallel

_BEACHES_PY = Path(__file__).resolve().parent.parent / "dashboard" / "beaches.py"
_BEACHES = {"Playa Uno": (18.0, -68.0), "Playa Dos": (19.0, -69.0)}
_MAX_BYTES_PER_MARKER = 60
_STYLE = {"color": "#5d4037", "fill": True, "fillColor": "#795548", "fillOpacity": 0.55, "weight": 1}


def _check(condition: bool, message: str) -> None:
    assert condition, message


def test_parallel_runs_together_and_waits() -> None:
    done: list[int] = []

    def task(number: int):
        def run() -> None:
            time.sleep(0.2)
            done.append(number)
        return run

    start = time.perf_counter()
    run_parallel([task(i) for i in range(4)])
    elapsed = time.perf_counter() - start
    _check(sorted(done) == [0, 1, 2, 3], "every task must finish before it returns")
    _check(elapsed < 0.6, f"4 tasks of 0.2 s took {elapsed:.2f} s, so they ran one by one")


def test_parallel_contains_failures() -> None:
    done: list[str] = []

    def boom() -> None:
        raise RuntimeError("the API is down")

    run_parallel([boom, lambda: done.append("ok")])
    _check(done == ["ok"], "one failed fetch must not stop the others or the page")
    run_parallel([])


def test_click_by_tooltip() -> None:
    click = {"last_object_clicked_tooltip": "🏖️ Playa Uno — La Altagracia"}
    _check(beach_from_map_click(click, _BEACHES) == "Playa Uno", "the tooltip names the beach")
    other = {"last_object_clicked": {"tooltip": "🏖️ Playa Dos — Samaná"}}
    _check(beach_from_map_click(other, _BEACHES) == "Playa Dos", "the clicked object can carry it too")


def test_click_by_coordinates() -> None:
    near = {"last_clicked": {"lat": 18.001, "lng": -68.001}}
    _check(beach_from_map_click(near, _BEACHES) == "Playa Uno", "a click on the beach selects it")
    far = {"last_clicked": {"lat": 18.1, "lng": -68.1}}
    _check(beach_from_map_click(far, _BEACHES) is None, "a click on open water selects nothing")


def test_click_on_a_sargassum_marker_selects_nothing() -> None:
    click = {"last_object_clicked_tooltip": "🟤 Sargazo · 1.20 km²", "last_clicked": {"lat": 25.0, "lng": -60.0}}
    _check(beach_from_map_click(click, _BEACHES) is None, "only beaches can be selected")


def test_click_ignores_filtered_out_and_malformed() -> None:
    hidden = {"last_object_clicked_tooltip": "🏖️ Playa Tres — Samaná"}
    _check(beach_from_map_click(hidden, _BEACHES) is None, "a beach the filters hide can't be selected")
    for result in (None, 5, "x", {}, {"last_clicked": "x"}, {"last_clicked": {"lat": "x", "lng": None}},
                   {"last_clicked": {}}, {"last_object_clicked": 5, "last_object_clicked_tooltip": 7}):
        _check(beach_from_map_click(result, _BEACHES) is None, f"should be ignored: {result!r}")
    _check(beach_from_map_click({"last_clicked": {"lat": 18, "lng": -68}}, {}) is None, "no beaches, no selection")


def _render(rows: list, label: str = "🟤 Sargazo · ") -> str:
    fmap = folium.Map(location=[19.0, -69.8], prefer_canvas=True)
    group = folium.FeatureGroup(name="x").add_to(fmap)
    CompactCircles(rows, _STYLE, label).add_to(group)
    return fmap.get_root().render()


def test_compact_circles_are_much_smaller() -> None:
    rows = [(18.0 + i / 100, -68.0 - i / 100, 5.5, 1.25) for i in range(350)]
    empty = len(_render([]).encode("utf-8"))
    compact = len(_render(rows).encode("utf-8")) - empty
    _check(compact / len(rows) <= _MAX_BYTES_PER_MARKER, f"{compact / len(rows):.0f} bytes per marker")
    fmap = folium.Map(location=[19.0, -69.8])
    group = folium.FeatureGroup(name="x").add_to(fmap)
    for lat, lon, radius, area in rows:
        folium.CircleMarker([lat, lon], radius=radius, tooltip=f"🟤 Sargazo · {area:.2f} km²").add_to(group)
    plain = len(fmap.get_root().render().encode("utf-8"))
    _check(compact * 5 < plain, f"compact {compact} bytes vs plain {plain} bytes")


def test_compact_circles_escape_their_text() -> None:
    html = _render([(18.0, -68.0, 5.0, 1.0)], label="</script><script>alert(1)</script>\"'")
    _check("<script>alert" not in html and "</script><script>alert" not in html, "the label must not break out of the script")
    _check(html.count("bindTooltip") == 1 and "\\u003c/script" in html, "the label should be encoded")


def test_compact_circles_keep_the_tooltip_format() -> None:
    html = _render([(18.0, -68.0, 5.0, 1.234)])
    _check('"🟤 Sargazo · "' in html and 'toFixed(2)+" km²"' in html and "sticky:true" in html, "tooltip format")
    _check("[18.0,-68.0,5.0,1.234]" in html, "the row data")


def test_beaches_script_uses_the_fast_paths() -> None:
    source = _BEACHES_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = {getattr(n.func, "id", ""): n for n in ast.walk(tree) if isinstance(n, ast.Call)}
    _check("run_parallel" in calls, "live data must load in parallel")
    folium_call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "st_folium")
    _check(any(k.arg == "on_change" for k in folium_call.keywords), "a click must not need a second rerun")
    _check("prefer_canvas=True" in source, "the map should draw on a canvas")
    _check('_cached_surf_conditions((_pb["name"],))' not in source, "a card must not wait for its own surf request")
    _check("st.rerun" not in source.split("_on_map_click", 1)[1].split("st_folium(", 1)[0], "the callback needs no rerun")


def main() -> int:
    logging.disable(logging.CRITICAL)
    tests = [(name, fn) for name, fn in globals().items() if name.startswith("test_") and callable(fn)]
    for name, test in tests:
        test()
        print(f"  ok {name}")
    print(f"All {len(tests)} map performance tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
