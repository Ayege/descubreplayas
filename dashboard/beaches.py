"""Streamlit beach explorer — tropical themed, full-map layout, bilingual (ES/EN).

Run from repo root:
    streamlit run dashboard/beaches.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import folium
import streamlit as st
from dotenv import load_dotenv
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium

from dashboard.beaches_data import (
    REGION_COLORS,
    all_activities,
    all_provinces,
    all_regions,
    beaches_with_maps,
)
from dashboard.risk_overlay import (
    RISK_COLORS,
    RISK_EMOJI,
    fetch_live_risk,
    risk_for_beach,
)

load_dotenv()

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")

st.set_page_config(
    page_title="Descubre Playas RD 🌴",
    page_icon="🌴",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# i18n — Español / English
# ---------------------------------------------------------------------------
_T = {
    "es": {
        "title": "🌴 Descubre Playas RD",
        "subtitle": "Tu guía completa de playas de la República Dominicana — 56 playas con acceso, actividades, fauna, sargazo en tiempo real y más",
        "filters_header": "🔎 Filtrar Playas",
        "region": "Región",
        "province": "Provincia",
        "activity": "Actividad",
        "protected_only": "🐢 Solo áreas protegidas",
        "free_only": "Solo entrada gratuita",
        "results": "{n} de {total} playas",
        "select_beach": "📍 Selecciona una playa",
        "best_time": "🗓️ Mejor época",
        "access": "🚪 Acceso",
        "entrance": "🎟️ Entrada",
        "parking": "🅿️ Estacionamiento",
        "yes": "Sí",
        "no_limited": "No / limitado",
        "water": "🌊 Agua",
        "activities": "🏄 Actividades",
        "wildlife": "🐠 Fauna",
        "facilities": "🏗️ Instalaciones",
        "ecosystem": "🌿 Ecosistema",
        "open_maps": "📍 Google Maps",
        "risk_header": "🌊 Riesgo de Sargazo",
        "risk_unavail": "Sin datos (API offline)",
        "nearest_zone": "zona más cercana",
        "away": "de distancia",
        "recommendations": "✨ También te puede gustar",
        "view_details": "Ver →",
        "tip": "💡 Haz clic en un marcador para ver detalles",
        "risk_legend": "Riesgo de sargazo",
        "no_match": "Ninguna playa coincide con los filtros.",
        "risk_none": "Sin riesgo",
        "risk_low": "Bajo",
        "risk_medium": "Medio",
        "risk_high": "Alto",
    },
    "en": {
        "title": "🌴 Discover DR Beaches",
        "subtitle": "Your complete guide to Dominican Republic beaches — 56 beaches with access info, activities, wildlife, live sargassum risk & more",
        "filters_header": "🔎 Filter Beaches",
        "region": "Region",
        "province": "Province",
        "activity": "Activity",
        "protected_only": "🐢 Protected areas only",
        "free_only": "Free entrance only",
        "results": "{n} of {total} beaches",
        "select_beach": "📍 Select a beach",
        "best_time": "🗓️ Best time",
        "access": "🚪 Access",
        "entrance": "🎟️ Entrance",
        "parking": "🅿️ Parking",
        "yes": "Yes",
        "no_limited": "No / limited",
        "water": "🌊 Water",
        "activities": "🏄 Activities",
        "wildlife": "🐠 Wildlife",
        "facilities": "🏗️ Facilities",
        "ecosystem": "🌿 Ecosystem",
        "open_maps": "📍 Google Maps",
        "risk_header": "🌊 Sargassum Risk",
        "risk_unavail": "No data (API offline)",
        "nearest_zone": "nearest zone",
        "away": "away",
        "recommendations": "✨ You might also like",
        "view_details": "View →",
        "tip": "💡 Click a marker to see details",
        "risk_legend": "Sargassum risk",
        "no_match": "No beaches match the current filters.",
        "risk_none": "None",
        "risk_low": "Low",
        "risk_medium": "Medium",
        "risk_high": "High",
    },
}

_RISK_LABELS = {
    "es": {"none": "Sin riesgo", "low": "Bajo", "medium": "Medio", "high": "Alto"},
    "en": {"none": "None", "low": "Low", "medium": "Medium", "high": "High"},
}

# ---------------------------------------------------------------------------
# CSS — responsive full-viewport map, tropical palette, clean sidebar
# ---------------------------------------------------------------------------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700;800;900&display=swap');

*, html, body, [class*="css"] { font-family: 'Nunito', sans-serif; box-sizing: border-box; }

/* ── Hide Streamlit chrome ── */
#MainMenu, footer, header { visibility: hidden; height: 0 !important; }

/* ── Zero-out default padding so map reaches edges ── */
.block-container {
    padding-top: 0 !important;
    padding-bottom: 0 !important;
    padding-left: 0 !important;
    padding-right: 0 !important;
    max-width: 100% !important;
}

/* ── App BG ── */
.stApp { background: #f0fafa; overflow-x: hidden; }

/* ── Map iframe: full viewport height on all screen sizes ── */
.stIframe, [data-testid="stIFrame"] iframe {
    height: calc(100vh - 4px) !important;
    min-height: 400px !important;
    width: 100% !important;
    border-radius: 0 !important;
    display: block !important;
}

/* ── Sidebar shell ── */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #00404a 0%, #005f6e 40%, #007a8c 75%, #009dae 100%) !important;
    box-shadow: 4px 0 24px rgba(0,64,74,.35);
    overflow-y: auto !important;
    max-height: 100vh !important;
}
[data-testid="stSidebar"] > div:first-child { padding-top: 1rem !important; }

/* ── Sidebar title ── */
[data-testid="stSidebar"] h1 {
    color: #ffffff !important;
    -webkit-text-fill-color: #fff !important;
    font-weight: 900 !important;
    font-size: clamp(1rem, 2.5vw, 1.35rem) !important;
    line-height: 1.2 !important;
    margin-bottom: 2px !important;
}

/* ── Sidebar text ── */
[data-testid="stSidebar"] * { color: #e0f7fa !important; }
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] .stMarkdown p {
    color: #cff0f4 !important;
    font-size: clamp(11px, 1.5vw, 13px) !important;
}
[data-testid="stSidebar"] .stCaption p {
    color: #9edde6 !important;
    font-size: clamp(10px, 1.2vw, 11px) !important;
}
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {
    color: #ffffff !important;
    -webkit-text-fill-color: #fff !important;
    margin: 10px 0 4px !important;
    font-size: clamp(0.8rem, 1.8vw, 0.95rem) !important;
}
[data-testid="stSidebar"] hr { border-color: rgba(255,255,255,.18) !important; margin: 8px 0 !important; }

/* ── Sidebar inputs ── */
[data-testid="stSidebar"] [data-testid="stMultiSelect"] > div > div,
[data-testid="stSidebar"] [data-testid="stSelectbox"] > div > div {
    background: rgba(255,255,255,.12) !important;
    border: 1px solid rgba(255,255,255,.22) !important;
    border-radius: 10px !important;
}

/* ── Sidebar toggle ── */
[data-testid="stSidebar"] [data-testid="stToggle"] > label {
    font-weight: 700 !important;
    font-size: clamp(11px, 1.5vw, 13px) !important;
}

/* ── Sidebar link button ── */
[data-testid="stSidebar"] .stLinkButton a {
    background: rgba(255,255,255,.16) !important;
    color: #fff !important;
    border: 1px solid rgba(255,255,255,.28) !important;
    border-radius: 20px !important;
    font-size: clamp(10px, 1.3vw, 12px) !important;
    padding: 4px 12px !important;
    text-decoration: none !important;
    display: inline-block;
}
[data-testid="stSidebar"] .stLinkButton a:hover { background: rgba(255,255,255,.28) !important; }

/* ── Risk banner ── */
.risk-banner {
    border-radius: 12px;
    padding: 8px 13px;
    font-weight: 700;
    font-size: clamp(11px, 1.4vw, 12.5px);
    margin: 5px 0;
    text-align: center;
    box-shadow: 0 2px 8px rgba(0,0,0,.18);
}

/* ── Strip Leaflet popup white box completely ── */
.leaflet-popup-content-wrapper {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    padding: 0 !important;
    border-radius: 0 !important;
}
.leaflet-popup-tip-container { display: none !important; }
.leaflet-popup-close-button { display: none !important; }
.leaflet-popup-content { margin: 0 !important; }

/* ── Right detail panel ── */
.detail-panel {
    background: linear-gradient(160deg,#00404a,#005f6e 50%,#007a8c);
    border-radius: 14px;
    padding: 16px 14px;
    height: calc(100vh - 8px);
    overflow-y: auto;
    color: #e0f7fa;
    font-family: 'Nunito', sans-serif;
}
.detail-panel h3 { color:#fff !important; font-size:1.05rem; margin:0 0 2px; }
.detail-panel .sub { color:#9edde6; font-size:11px; margin:0 0 8px; }
.detail-panel .label { color:#b2ebf2; font-size:11px; font-weight:700; margin:8px 0 1px; }
.detail-panel .val { color:#e0f7fa; font-size:12px; margin:0 0 4px; line-height:1.4; }
.detail-panel hr { border-color:rgba(255,255,255,.15); margin:10px 0; }
.detail-panel .maps-btn {
    display:block; text-align:center;
    background:rgba(255,255,255,.18); color:#fff !important;
    border:1px solid rgba(255,255,255,.3); border-radius:20px;
    padding:6px; font-size:12px; font-weight:800; text-decoration:none;
    margin-top:10px;
}
.detail-panel .maps-btn:hover { background:rgba(255,255,255,.3); }

/* ── Tablet: compress sidebar padding ── */
@media (max-width: 1024px) {
    [data-testid="stSidebar"] > div:first-child {
        padding-left: 0.75rem !important;
        padding-right: 0.75rem !important;
    }
    .stIframe, [data-testid="stIFrame"] iframe {
        min-height: 350px !important;
    }
}

/* ── Mobile: sidebar becomes a drawer (Streamlit handles collapse),
      map fills remaining space and we ensure readable text ── */
@media (max-width: 768px) {
    .block-container { padding: 0 !important; }
    [data-testid="stSidebar"] h1 { font-size: 1.1rem !important; }
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] .stMarkdown p { font-size: 13px !important; }
    .stIframe, [data-testid="stIFrame"] iframe {
        height: 60vh !important;
        min-height: 300px !important;
    }
    .risk-banner { font-size: 12px; padding: 7px 10px; }
}

/* ── Very small screens ── */
@media (max-width: 480px) {
    [data-testid="stSidebar"] h1 { font-size: 1rem !important; }
    .stIframe, [data-testid="stIFrame"] iframe {
        height: 50vh !important;
        min-height: 260px !important;
    }
}
</style>
""", unsafe_allow_html=True)

BEACHES = beaches_with_maps()

# ---------------------------------------------------------------------------
# Language selector (top of sidebar)
# ---------------------------------------------------------------------------
with st.sidebar:
    _en = st.toggle("🇺🇸 English", value=False, key="lang_en")
    lang = "en" if _en else "es"
    L = _T[lang]
    RISK_LABEL = _RISK_LABELS[lang]
    # App branding inside sidebar
    st.markdown(
        f"<h1 style='margin-top:4px'>{L['title']}</h1>"
        f"<p style='font-size:11px;color:#9edde6;margin:0 0 8px;line-height:1.4'>{L['subtitle']}</p>",
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# Live risk fetch
# ---------------------------------------------------------------------------
zones: list[dict] = []
risk_by_zone_id: dict[int, str] = {}
zones, risk_by_zone_id = fetch_live_risk(API_BASE_URL)


def _beach_risk(beach: dict):
    if not zones:
        return None, None, None
    return risk_for_beach(beach, zones, risk_by_zone_id)


# ---------------------------------------------------------------------------
# Sidebar — filters
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown(f"### {L['filters_header']}")
    sel_regions = st.multiselect(L["region"], all_regions(), default=all_regions())
    sel_provinces = st.multiselect(L["province"], all_provinces(), default=[])
    sel_activities = st.multiselect(L["activity"], all_activities(), default=[])
    protected_only = st.checkbox(L["protected_only"], value=False)
    free_only = st.checkbox(L["free_only"], value=False)
    if not zones:
        st.caption(f"🌊 {L['risk_unavail']}")


def _matches(beach: dict) -> bool:
    if sel_regions and beach["region"] not in sel_regions:
        return False
    if sel_provinces and beach["province"] not in sel_provinces:
        return False
    if sel_activities and not set(sel_activities).issubset(set(beach["activities"])):
        return False
    if protected_only and not beach["protected_area"]:
        return False
    if free_only and "free" not in beach["entrance_fee"].lower():
        return False
    return True


filtered = [b for b in BEACHES if _matches(b)]
filtered_names = {b["name"] for b in filtered}

with st.sidebar:
    st.caption(L["results"].format(n=len(filtered), total=len(BEACHES)))

# ---------------------------------------------------------------------------
# Build Folium map
# ---------------------------------------------------------------------------
m = folium.Map(location=[19.0, -69.8], zoom_start=7, tiles="CartoDB Voyager")
cluster = MarkerCluster(
    options={"maxClusterRadius": 50, "disableClusteringAtZoom": 11}
).add_to(m)

for b in filtered:
    color = REGION_COLORS.get(b["region"], "#1f77b4")
    risk_level, near_zone, dist_km_b = _beach_risk(b)
    if risk_level is not None:
        color = RISK_COLORS.get(risk_level, color)
    turtle_icon = " 🐢" if b["protected_area"] else ""
    desc_short = (b["description"] or "")[:170].rstrip()
    if len(b["description"] or "") > 170:
        desc_short += "…"

    # Risk badge — always present
    if risk_level is not None and near_zone:
        rc = RISK_COLORS.get(risk_level, "#6c757d")
        label_txt = RISK_LABEL.get(risk_level, risk_level.upper())
        risk_badge = (
            f"<div style='background:{rc};color:#fff;display:inline-block;"
            f"padding:3px 12px;border-radius:20px;font-size:11px;font-weight:700;margin:5px 0'>"
            f"🌊 Sargazo: {label_txt} · {near_zone['name']} (~{dist_km_b:.0f} km)"
            f"</div>"
        )
    else:
        risk_badge = (
            "<div style='background:#90a4ae;color:#fff;display:inline-block;"
            "padding:3px 12px;border-radius:20px;font-size:11px;font-weight:700;margin:5px 0'>"
            "🌊 Sargazo: sin datos / no data"
            "</div>"
        )

    popup_html = (
        f"<div style='font-family:\"Nunito\",sans-serif;min-width:220px;max-width:280px;"
        f"border-radius:14px;overflow:hidden;box-shadow:0 6px 24px rgba(0,0,0,.28)'>"
        # teal header only — no white box
        f"<div style='background:linear-gradient(135deg,#005f73,#0a9396);padding:12px 14px'>"
        f"<div style='color:#fff;font-size:15px;font-weight:900'>{b['name']}{turtle_icon}</div>"
        f"<div style='color:#b2ebf2;font-size:11px;margin-top:2px'>{b['province']} · {b['region']}</div>"
        f"<div style='margin-top:7px'>{risk_badge}</div>"
        f"<a href='{b['google_maps_url']}' target='_blank' "
        f"style='display:block;text-align:center;background:rgba(255,255,255,.22);"
        f"color:#fff;border-radius:20px;padding:5px;font-size:11px;font-weight:800;"
        f"margin-top:8px;text-decoration:none'>📍 Google Maps ↗</a>"
        f"</div></div>"
    )
    folium.CircleMarker(
        location=[b["latitude"], b["longitude"]],
        radius=10,
        color=color,
        fill=True,
        fill_color=color,
        fill_opacity=0.85,
        weight=2,
        popup=folium.Popup(popup_html, max_width=340),
        tooltip=f"🏖️ {b['name']} — {b['province']}",
    ).add_to(cluster)

# Floating legend (bottom-left inside map)
if zones:
    legend_rows = "".join(
        f"<div style='display:flex;align-items:center;gap:7px;margin:3px 0'>"
        f"<div style='width:13px;height:13px;border-radius:50%;background:{c};flex-shrink:0'></div>"
        f"<span style='font-size:12px;color:#37474f'>{RISK_LABEL.get(lv, lv)}</span></div>"
        for lv, c in RISK_COLORS.items()
    )
    legend_title = L["risk_legend"]
else:
    legend_rows = "".join(
        f"<div style='display:flex;align-items:center;gap:7px;margin:3px 0'>"
        f"<div style='width:13px;height:13px;border-radius:50%;background:{c};flex-shrink:0'></div>"
        f"<span style='font-size:12px;color:#37474f'>{r}</span></div>"
        for r, c in REGION_COLORS.items()
    )
    legend_title = "Regiones / Regions"

m.get_root().html.add_child(folium.Element(
    f"<div style='position:absolute;bottom:28px;left:10px;z-index:1000;"
    f"background:rgba(255,255,255,.93);border-radius:12px;padding:10px 14px;"
    f"box-shadow:0 2px 14px rgba(0,0,0,.18);backdrop-filter:blur(6px)'>"
    f"<div style='font-weight:800;font-size:12px;color:#005f73;margin-bottom:5px'>{legend_title}</div>"
    f"{legend_rows}</div>"
))
# Floating tip (top-right inside map)
m.get_root().html.add_child(folium.Element(
    f"<div style='position:absolute;top:10px;right:10px;z-index:1000;"
    f"background:rgba(0,96,100,.88);color:#fff;border-radius:10px;"
    f"padding:8px 14px;font-size:12px;font-weight:700;backdrop-filter:blur(4px)'>"
    f"{L['tip']}</div>"
))

# ---------------------------------------------------------------------------
# Detail overlay panel — built as Folium HTML so it sits ON the map
# ---------------------------------------------------------------------------
if filtered:
    names_sorted = sorted(filtered, key=lambda x: x["name"])
    default_name = st.session_state.get("selected_beach")
    if default_name not in {b["name"] for b in filtered}:
        default_name = names_sorted[0]["name"]
    panel_beach = next(b for b in filtered if b["name"] == default_name)
    turtle_icon = " 🐢" if panel_beach["protected_area"] else ""
    risk_level, near_zone, dist_km = _beach_risk(panel_beach)

    if risk_level is not None and near_zone:
        rc = RISK_COLORS.get(risk_level, "#607d8b")
        lbl = RISK_LABEL.get(risk_level, risk_level.upper())
        risk_html = (
            f"<div style='background:{rc};border-radius:8px;padding:6px 10px;"
            f"font-weight:700;font-size:11px;text-align:center;margin:7px 0;"
            f"box-shadow:0 2px 6px rgba(0,0,0,.25)'>"
            f"🌊 {lbl} · {near_zone['name']}<br>"
            f"<span style='font-weight:400;font-size:10px'>~{dist_km:.0f} km {L['away']}</span></div>"
        )
    else:
        risk_html = (
            f"<div style='background:#546e7a;border-radius:8px;padding:6px 10px;"
            f"font-weight:700;font-size:11px;text-align:center;margin:7px 0'>"
            f"🌊 {L['risk_unavail']}</div>"
        )

    def _row(label: str, val: str) -> str:
        return (
            f"<div style='margin:5px 0'>"
            f"<div style='color:#9edde6;font-size:10px;font-weight:700;text-transform:uppercase;"
            f"letter-spacing:.4px'>{label}</div>"
            f"<div style='color:#e0f7fa;font-size:11.5px;line-height:1.35'>{val}</div>"
            f"</div>"
        )

    panel_html = (
        # position:fixed in an iframe = fixed to the iframe viewport (always top-right)
        f"<div id='beach-detail-panel' style='"
        f"position:fixed;top:0;right:0;width:290px;height:100vh;z-index:9999;"
        f"background:linear-gradient(180deg,#002b33 0%,#00404a 30%,#005f6e 65%,#007a8c 100%);"
        f"overflow-y:auto;padding:14px 13px;box-sizing:border-box;"
        f"box-shadow:-6px 0 28px rgba(0,0,0,.45);font-family:Nunito,sans-serif'>"
        # header
        f"<div style='font-size:15px;font-weight:900;color:#fff;line-height:1.2'>"
        f"{panel_beach['name']}{turtle_icon}</div>"
        f"<div style='font-size:11px;color:#9edde6;margin:2px 0 4px'>"
        f"{panel_beach['province']} · {panel_beach['region']}</div>"
        f"{risk_html}"
        f"<div style='font-size:11px;color:#b2ebf2;margin:0 0 8px;line-height:1.4'>"
        f"{panel_beach['description'][:180]}{'…' if len(panel_beach['description'])>180 else ''}</div>"
        f"<hr style='border-color:rgba(255,255,255,.15);margin:8px 0'>"
        + _row(L["best_time"], panel_beach["best_time_to_visit"])
        + _row(L["entrance"], panel_beach["entrance_fee"])
        + _row(L["parking"], ("✅ " + L["yes"]) if panel_beach["parking"] else ("⚠️ " + L["no_limited"]))
        + _row(L["water"], panel_beach["water_conditions"])
        + _row(L["access"], f"{panel_beach['access_type']} — {panel_beach['access_description']}")
        + f"<hr style='border-color:rgba(255,255,255,.15);margin:8px 0'>"
        + _row(L["activities"], ", ".join(panel_beach["activities"]))
        + _row(L["wildlife"], ", ".join(panel_beach["wildlife"]) if panel_beach["wildlife"] else "N/A")
        + _row(L["facilities"], ", ".join(panel_beach["facilities"]))
        + _row(L["ecosystem"], panel_beach["ecosystem"])
        + f"<a href='{panel_beach['google_maps_url']}' target='_blank' "
        f"style='display:block;text-align:center;background:rgba(255,255,255,.18);"
        f"color:#fff;border:1px solid rgba(255,255,255,.28);border-radius:20px;"
        f"padding:7px;font-size:12px;font-weight:800;text-decoration:none;margin-top:10px'>"
        f"📍 {L['open_maps']} ↗</a>"
        f"</div>"
    )
    m.get_root().html.add_child(folium.Element(panel_html))

    # Center map on selected beach so it stays meaningful after rerun
    m.location = [panel_beach["latitude"], panel_beach["longitude"]]
    m.zoom_start = 10

# Render full-width map — key forces re-render when selected beach changes
_map_key = f"bmap_{st.session_state.get('selected_beach', '_none_')}"
map_result = st_folium(
    m,
    width="100%",
    height=900,
    returned_objects=["last_object_clicked_tooltip"],
    key=_map_key,
)

# Sync map click → session state
if map_result and map_result.get("last_object_clicked_tooltip"):
    tooltip_text: str = map_result["last_object_clicked_tooltip"]
    clicked = tooltip_text.replace("🏖️ ", "").split(" — ")[0].strip()
    if clicked in filtered_names:
        st.session_state["selected_beach"] = clicked
        st.rerun()

