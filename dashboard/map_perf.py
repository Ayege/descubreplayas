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
    name = label.split(TOOLTIP_SEPARATOR)[0].strip()
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


FLY_DURATION_S = 1.0
# The view the map opens with arrives right after it's built, and shouldn't fly.
FLY_AFTER_LOAD_MS = 1500
# flyTo divides by the distance moved, so it fails if the centre stays put.
FLY_MIN_DISTANCE_M = 5
# Moves at this zoom or closer are to one beach, so its card is open. Area views
# (filters) must stay below it.
CARD_ZOOM = 13
# How much of the map the beach card and report card cover. They mirror the CSS
# in beaches.py (.beach-detail and .st-key-beach_report_card): change both together.
CARD_TABLET_MAX_PX = 1024
CARD_PHONE_MAX_PX = 768
CARD_COVER_DESKTOP_PX = 595   # report card right:323px + width:272px
CARD_COVER_TABLET_PX = 536    # report card right:288px + width:248px
CARD_SHEET_VH = 0.36          # phone bottom sheet: max-height 36vh
CARD_STRIP_PX = 90            # phone report strip above the sheet
# On phones the beach sits this much lower than the middle of the free map, so the
# popup that opens above it clears the Filtros pill at the top.
CARD_POPUP_ROOM_PX = 120
# When the cards leave almost no free map (a narrow window with the sidebar open),
# the shift is cut short so the beach stays at least this far inside the map.
CARD_EDGE_PX = 48


class SmoothMapMoves(MacroElement):
    """Fly to a new view instead of jumping, for moves the app asks for.

    st_folium calls map.setView(center, zoom) on a live map. Leaflet's own calls (zoom
    buttons, scroll wheel, panTo, fitBounds) always pass a third argument, so testing
    for exactly two leaves the visitor's own panning and zooming untouched.

    A beach-level move (zoom >= CARD_ZOOM) opens the beach card, which covers part of
    the map. The target is shifted by half the covered size so the beach lands in the
    middle of the map that stays visible, right beside the cards: to the left of them
    on wider screens, above the bottom sheet on phones. The screen size is read in the
    browser because the server never learns it. A beach move never zooms out: a
    visitor already closer than CARD_ZOOM keeps their zoom.
    """

    _template = Template(
        "{% macro script(this, kwargs) %}"
        "(function(){var m={{this._parent.get_name()}},o=m.setView,b=Date.now();"
        "var r=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;"
        "function f(c,z){var w=window;"
        "try{if(window.parent.innerWidth)w=window.parent}catch(e){}"
        "var vw=w.innerWidth,s=m.getSize(),dx=0,dy=0;"
        "if(vw>{{this.tablet_max}})dx={{this.cover_desktop}}/2;"
        "else if(vw>{{this.phone_max}})dx={{this.cover_tablet}}/2;"
        "else dy=(w.innerHeight*{{this.sheet_vh}}+{{this.strip_px}}-{{this.popup_room}})/2;"
        "dx=Math.max(0,Math.min(dx,s.x/2-{{this.edge_px}}));"
        "dy=Math.max(0,Math.min(dy,s.y/2-{{this.edge_px}}));"
        "return m.unproject(m.project(L.latLng(c),z).add([dx,dy]),z)}"
        "m.setView=function(c,z){"
        "if(arguments.length!==2)return o.apply(m,arguments);"
        "if(z>={{this.card_zoom}}){z=Math.max(z,m.getZoom());c=f(c,z)}"
        "if(r||Date.now()-b<{{this.after_load_ms}}"
        "||m.getCenter().distanceTo(c)<{{this.min_distance_m}})return o.call(m,c,z);"
        "return m.flyTo(c,z,{duration:{{this.duration_s}}})}})();"
        "{% endmacro %}"
    )

    def __init__(self) -> None:
        super().__init__()
        self._name = "SmoothMapMoves"
        self.duration_s = FLY_DURATION_S
        self.after_load_ms = FLY_AFTER_LOAD_MS
        self.min_distance_m = FLY_MIN_DISTANCE_M
        self.card_zoom = CARD_ZOOM
        self.tablet_max = CARD_TABLET_MAX_PX
        self.phone_max = CARD_PHONE_MAX_PX
        self.cover_desktop = CARD_COVER_DESKTOP_PX
        self.cover_tablet = CARD_COVER_TABLET_PX
        self.sheet_vh = CARD_SHEET_VH
        self.strip_px = CARD_STRIP_PX
        self.popup_room = CARD_POPUP_ROOM_PX
        self.edge_px = CARD_EDGE_PX


CARD_MOTION_A = "card-motion-a"
CARD_MOTION_B = "card-motion-b"


def next_card_motion(last_beach: str | None, last_class: str | None, beach: str) -> tuple[str, str]:
    """The beach and animation class for this render. The class flips only when the
    beach changes: a changed animation name replays the card's entrance, so an
    unrelated rerun doesn't replay it.
    """
    if beach == last_beach and last_class in (CARD_MOTION_A, CARD_MOTION_B):
        return beach, last_class
    return beach, CARD_MOTION_B if last_class == CARD_MOTION_A else CARD_MOTION_A
