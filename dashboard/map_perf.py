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

"""Small helpers that keep the beach map and card fast.

Kept out of beaches.py, which is a script and cannot be imported by tests.
"""
from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor

from branca.element import MacroElement
from jinja2 import Template
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

from dashboard.risk_overlay import haversine_km

logger = logging.getLogger(__name__)

# A click within this distance of a beach selects it, to avoid selecting on a misclick.
MAX_CLICK_DISTANCE_KM = 0.6
TOOLTIP_PREFIX = "🏖️ "
TOOLTIP_SEPARATOR = " — "


def run_parallel(tasks: Sequence[Callable[[], object]]) -> None:
    """Run fetches at the same time, and wait for all of them.

    Each fetch has its own timeout and handles its own failures. Anything that
    still escapes is logged here: one failed fetch must never stop the page.
    """
    context = get_script_run_ctx(suppress_warning=True)

    def run(task: Callable[[], object]) -> None:
        if context is not None:
            add_script_run_ctx(threading.current_thread(), context)
        try:
            task()
        except Exception:  # noqa: BLE001 (the page must render whatever a fetch does)
            logger.exception("Background fetch failed")

    with ThreadPoolExecutor(max_workers=max(1, len(tasks))) as pool:
        list(pool.map(run, tasks))


def _name_from_label(label: object, names: Mapping[str, object]) -> str | None:
    if not isinstance(label, str):
        return None
    name = label.replace(TOOLTIP_PREFIX, "").split(TOOLTIP_SEPARATOR)[0].strip()
    return name if name in names else None


def _nearest_beach(point: object, beaches: Mapping[str, tuple[float, float]]) -> str | None:
    if not isinstance(point, Mapping):
        return None
    try:
        lat = float(point.get("lat") or point.get("latitude"))
        lon = float(point.get("lng") or point.get("lon") or point.get("longitude"))
    except (TypeError, ValueError):
        return None
    distances = ((haversine_km(lat, lon, *where), name) for name, where in beaches.items())
    distance, name = min(distances, default=(float("inf"), None))
    return name if distance <= MAX_CLICK_DISTANCE_KM else None


def beach_from_map_click(result: object, beaches: Mapping[str, tuple[float, float]]) -> str | None:
    """Which beach a map click means: its tooltip first, then its coordinates."""
    if not isinstance(result, Mapping):
        return None
    clicked = result.get("last_object_clicked")
    labels: list[object] = [result.get("last_object_clicked_tooltip")]
    if isinstance(clicked, Mapping):
        labels += [clicked.get(key) for key in ("tooltip", "name", "title", "popup")]
    for label in labels:
        name = _name_from_label(label, beaches)
        if name:
            return name
    return _nearest_beach(result.get("last_clicked"), beaches)


def _json_for_script(value: object) -> str:
    """JSON that is safe inside a <script> block."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


class CompactCircles(MacroElement):
    """Many circle markers sharing one style, sent as data and one small loop.

    Each folium.CircleMarker with a tooltip adds over 1 KB of generated JavaScript,
    and all of it travels to the browser again on every rerun.
    """

    _template = Template(
        "{% macro script(this, kwargs) %}"
        "(function(){var s={{this.style_json}};var t={{this.label_json}};"
        "{{this.rows_json}}.forEach(function(p){"
        "L.circleMarker([p[0],p[1]],Object.assign({radius:p[2]},s))"
        ".bindTooltip(t+p[3].toFixed(2)+\" km²\",{sticky:true})"
        ".addTo({{this._parent.get_name()}})})})();"
        "{% endmacro %}"
    )

    def __init__(self, rows: Iterable[tuple[float, float, float, float]], style: Mapping[str, object], label: str) -> None:
        """rows are (lat, lon, radius, area_km2); label comes before the area."""
        super().__init__()
        self._name = "CompactCircles"
        self.rows_json = _json_for_script([[lat, lon, round(radius, 2), round(area, 4)] for lat, lon, radius, area in rows])
        self.style_json = _json_for_script(style)
        self.label_json = _json_for_script(label)
