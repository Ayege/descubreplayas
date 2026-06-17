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
    beach_good_in_month,
    beaches_with_maps,
    provinces_for_regions,
    region_for_province,
)
from dashboard.risk_overlay import (
    RISK_COLORS,
    RISK_EMOJI,
    fetch_detections,
    fetch_live_risk,
    risk_for_beach,
)
from dashboard.climatology import seasonal_index, seasonal_risk

load_dotenv()

API_BASE_URL = os.environ.get("API_BASE_URL", "https://api.descubreplayas.com.do")

st.set_page_config(
    page_title="Descubre Playas RD",
    page_icon="🌴",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# i18n — Español / English
# ---------------------------------------------------------------------------
_T = {
    "es": {
        "title": "🌴 Descubre Playas RD 🇩🇴",
        "subtitle": "Tu guía completa de playas de la República Dominicana — 56 playas con acceso, actividades, fauna, sargazo en tiempo real y más. Impulsando el ecoturismo responsable mediante un algoritmo propio que cruza datos geoespaciales de acceso y registros de biodiversidad para proteger las costas dominicanas.",
        "filters_header": "Filtrar Playas",
        "region": "Región",
        "province": "Provincia",
        "activity": "Actividad",
        "protected_only": "Solo áreas protegidas",
        "free_only": "Solo entrada gratuita",
        "month_filter": "🗓️ Fecha de visita",
        "month_note": "Filtra por mejor época. El riesgo de sargazo refleja condiciones actuales (72 h).",
        "show_zones": "🌊 Mostrar zonas de monitoreo de Sargazo",
        "show_masses": "🟤 Mostrar masas de sargazo detectadas",
        "masses_unavail": "Sin detecciones (ejecuta el pipeline)",
        "horizon": "⏱️ Horizonte de pronóstico",
        "horizon_note": "El pronóstico de sargazo es confiable solo ~72h. Más allá usa la temporada.",
        "horizon_now": "Ahora",
        "season_note": "📅 Temporada alta de sargazo en el Caribe: marzo–agosto.",
        "prediction_info_title": "ℹ️ Métodos de predicción",
        "prediction_physics": "Física (0-72h): Deriva lagrangiana + corrientes oceánicas. Preciso.",
        "prediction_seasonal": "Temporada: Climatología histórica (mar-ago = alta). No predice eventos específicos.",
        "prediction_date_note": "⚠️ Hasta 3 días: pronóstico físico (deriva). Más lejos: estimación estacional (climatología), no un pronóstico exacto.",
        "beach_legend": "Playas por región",
        "zone_legend": "Riesgo de sargazo (zonas)",
        "seasonal_badge": "Estimación estacional",
        "physics_badge": "Pronóstico (deriva)",
        "method_physics": "Método: deriva física · Confianza alta",
        "method_seasonal": "Método: climatología estacional · Confianza: estimación",
        "seasonal_advisory": "Estimación basada en el ciclo anual típico del Caribe para este mes y costa. No es un pronóstico exacto.",
        "results": "{n} de {total} playas",
        "select_beach": "📍 Selecciona una playa",
        "best_time": "Mejor época",
        "access": "Acceso",
        "entrance": "Entrada",
        "parking": "Estacionamiento",
        "yes": "Sí",
        "no_limited": "No / limitado",
        "water": "Agua",
        "activities": "Actividades",
        "wildlife": "Fauna",
        "facilities": "Instalaciones",
        "ecosystem": "Ecosistema",
        "open_maps": "Google Maps",
        "risk_header": "Riesgo de Sargazo",
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
        "month_filter": "🗓️ Visit date",
        "month_note": "Filters by best season. Sargassum risk reflects current conditions (72 h forecast).",
        "show_zones": "🌊 Show monitoring zones",
        "show_masses": "🟤 Show detected sargassum masses",
        "masses_unavail": "No detections (run the pipeline)",
        "horizon": "⏱️ Forecast horizon",
        "horizon_note": "Sargassum forecast is reliable only ~72h. Beyond that, use the season.",
        "horizon_now": "Now",
        "season_note": "📅 Caribbean sargassum peak season: March–August.",
        "prediction_info_title": "ℹ️ Prediction methods",
        "prediction_physics": "Physics (0-72h): Lagrangian drift + ocean currents. Accurate.",
        "prediction_seasonal": "Seasonal: Historical climatology (Mar-Aug = peak). Does not predict specific events.",
        "prediction_date_note": "⚠️ Within 3 days: physics (drift) forecast. Further out: seasonal estimate (climatology), not an exact forecast.",
        "beach_legend": "Beaches by region",
        "zone_legend": "Sargassum risk (zones)",
        "seasonal_badge": "Seasonal estimate",
        "physics_badge": "Forecast (drift)",
        "method_physics": "Method: physics drift · High confidence",
        "method_seasonal": "Method: seasonal climatology · Confidence: estimate",
        "seasonal_advisory": "Estimate based on the Caribbean's typical annual cycle for this month and coast. Not an exact forecast.",
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
    "es": {"none": "Sin riesgo", "low": "Bajo", "medium": "Medio", "high": "Alto",
           "out": "Fuera de cobertura"},
    "en": {"none": "None", "low": "Low", "medium": "Medium", "high": "High",
           "out": "Out of range"},
}

# A beach only inherits a zone's risk if it lies within this radius of the
# zone centre. Zone boxes are 0.5° half-width (~55 km to the edge, ~76 km to a
# corner), so 85 km keeps all in-zone beaches covered while excluding distant
# ones. Beyond this, the beach is outside the monitored area.
COVERAGE_KM = 85.0

# ---------------------------------------------------------------------------
# CSS — responsive full-viewport map, tropical palette, clean sidebar
# ---------------------------------------------------------------------------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700;800;900&display=swap');

*, html, body, [class*="css"] { font-family: 'Nunito', sans-serif; box-sizing: border-box; }

/* ── Hide Streamlit chrome WITHOUT killing the sidebar toggle ──
   NOTE: we must NOT `display:none` the whole header / stHeader, because the
   button that re-opens a collapsed sidebar lives inside it. Hiding the header
   outright means that once the sidebar is closed (esp. on mobile) it can never
   be reopened. So we only hide the menu/toolbar/deploy/status widgets and make
   the header itself transparent + non-blocking. */
#MainMenu,
footer,
[data-testid="stToolbar"],
[data-testid="stDecoration"],
[data-testid="stDeployButton"],
[data-testid="stStatusWidget"] {
    display: none !important;
    visibility: hidden !important;
    height: 0 !important;
    min-height: 0 !important;
    overflow: hidden !important;
}

/* Header: keep in the DOM (for the sidebar toggle) but visually collapsed and
   click-through so it doesn't block the map underneath. */
header,
[data-testid="stHeader"] {
    background: transparent !important;
    height: 0 !important;
    min-height: 0 !important;
    box-shadow: none !important;
    pointer-events: none !important;
}

/* …but the sidebar open/collapse controls MUST stay visible and clickable. */
[data-testid="stSidebarCollapsedControl"],
[data-testid="collapsedControl"],
[data-testid="stSidebarCollapseButton"],
[data-testid="stExpandSidebarButton"],
[data-testid="baseButton-headerNoPadding"] {
    display: flex !important;
    visibility: visible !important;
    pointer-events: auto !important;
    opacity: 1 !important;
    z-index: 1000000 !important;
}

/* The floating "reopen sidebar" button (shown when collapsed): make it an
   obvious, tappable pill that sits above the map on every screen size. */
[data-testid="stSidebarCollapsedControl"],
[data-testid="collapsedControl"] {
    position: fixed !important;
    top: 10px !important;
    left: 10px !important;
    background: rgba(0,96,100,.92) !important;
    border-radius: 10px !important;
    padding: 4px 6px !important;
    box-shadow: 0 2px 10px rgba(0,0,0,.3) !important;
}
[data-testid="stSidebarCollapsedControl"] svg,
[data-testid="collapsedControl"] svg {
    color: #fff !important;
    fill: #fff !important;
}

/* ── Zero-out ALL wrappers so map reaches the very top ── */
.block-container,
[data-testid="stMainBlockContainer"],
section.main > div:first-child,
section[data-testid="stMain"],
.stMainBlockContainer {
    padding-top: 0 !important;
    padding-bottom: 0 !important;
    padding-left: 0 !important;
    padding-right: 0 !important;
    max-width: 100% !important;
    margin-top: 0 !important;
}

/* ── Nuke any residual top margin on the first Streamlit vertical block ── */
[data-testid="stVerticalBlock"] > div:first-child,
[data-testid="stVerticalBlockBorderWrapper"] {
    margin-top: 0 !important;
    padding-top: 0 !important;
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

/* ── Floating beach detail panel (desktop: right rail) ── */
.beach-detail {
    position: fixed; right: 16px; top: 60px;
    width: 295px; max-height: calc(100vh - 80px);
    overflow-y: auto; overflow-x: hidden;
    background: linear-gradient(160deg,#001f26 0%,#003540 30%,#005060 65%,#006878 100%);
    border-radius: 22px; padding: 18px 16px 16px;
    box-shadow: 0 12px 50px rgba(0,0,0,.6), 0 0 0 1px rgba(255,255,255,.1);
    font-family: 'Nunito', sans-serif; z-index: 99999; color: #e0f7fa;
}

/* ── Floating map legend (desktop: bottom-center) ── */
.map-legend {
    position: fixed; bottom: 16px; left: 50%; transform: translateX(-50%);
    z-index: 99998; max-height: 35vh; overflow-y: auto;
    background: rgba(255,255,255,.97); border-radius: 14px; padding: 10px 16px;
    box-shadow: 0 4px 28px rgba(0,0,0,.3); font-family: 'Nunito', sans-serif;
    border: 1px solid rgba(0,95,115,.14); min-width: 180px;
}

/* ── Tablet: compress sidebar padding ── */
@media (max-width: 1024px) {
    [data-testid="stSidebar"] > div:first-child {
        padding-left: 0.75rem !important;
        padding-right: 0.75rem !important;
    }
    .stIframe, [data-testid="stIFrame"] iframe {
        min-height: 350px !important;
    }
    .beach-detail { width: 260px; }
}

/* ── Mobile: sidebar becomes a drawer (Streamlit handles collapse),
      map fills the top, detail panel becomes a bottom sheet ── */
@media (max-width: 768px) {
    .block-container { padding: 0 !important; }
    [data-testid="stSidebar"] h1 { font-size: 1.1rem !important; }
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] .stMarkdown p { font-size: 13px !important; }
    .stIframe, [data-testid="stIFrame"] iframe {
        height: 62vh !important;
        min-height: 320px !important;
    }
    .risk-banner { font-size: 12px; padding: 7px 10px; }

    /* Detail panel → full-width bottom sheet, kept SMALLER than the map so the
       map remains the dominant, usable area on phones. */
    .beach-detail {
        right: 0 !important; left: 0 !important;
        top: auto !important; bottom: 0 !important;
        width: 100% !important; max-width: 100% !important;
        max-height: 36vh !important;
        border-radius: 20px 20px 0 0 !important;
        padding: 14px 16px 18px !important;
        box-shadow: 0 -8px 40px rgba(0,0,0,.55) !important;
    }
    /* Legend → top-RIGHT on mobile so it never collides with the sidebar
       reopen button (top-left) or the bottom sheet. */
    .map-legend {
        top: 54px !important; bottom: auto !important;
        left: auto !important; right: 8px !important; transform: none !important;
        max-height: 26vh !important; padding: 7px 11px !important;
        min-width: 0 !important; font-size: 11px;
    }
}

/* ── Very small screens ── */
@media (max-width: 480px) {
    [data-testid="stSidebar"] h1 { font-size: 1rem !important; }
    .stIframe, [data-testid="stIFrame"] iframe {
        height: 60vh !important;
        min-height: 300px !important;
    }
    .beach-detail { max-height: 36vh !important; padding: 12px 14px 16px !important; }
    .map-legend { max-height: 20vh !important; }
}</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Static beach dataset — cached so the 56-beach list (with Google Maps URLs)
# is built once per session instead of on every rerun / beach click.
# ---------------------------------------------------------------------------
@st.cache_data
def _load_beaches() -> list[dict]:
    return beaches_with_maps()


BEACHES = _load_beaches()

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
# Cached API helpers — TTL 5 min so beach-click reruns don't re-hit the API.
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300)
def _cached_fetch_live_risk(url: str):
    return fetch_live_risk(url)


@st.cache_data(ttl=300)
def _cached_fetch_detections(url: str):
    # Fetch a bounded number of masses; the map only needs the significant
    # ones, and a smaller payload means faster JSON transfer + render.
    return fetch_detections(url, limit=800)


# ---------------------------------------------------------------------------
# Live risk fetch
# ---------------------------------------------------------------------------
zones: list[dict] = []
risk_by_zone_id: dict[int, str] = {}
zones, forecast_by_zone_id = _cached_fetch_live_risk(API_BASE_URL)


def _beach_risk(beach: dict):
    """Return (risk_level, nearest_zone, dist_km, forecast_dict) or (None,None,None,None).

    The returned risk_level reflects the currently-selected forecast horizon.
    """
    if not zones:
        return None, None, None, None
    lvl, zone, dist, fc = risk_for_beach(beach, zones, forecast_by_zone_id)
    # Distance gate: a beach far from every monitored zone must NOT inherit that
    # zone's risk (e.g. a Southwest beach 239 km from Puerto Plata is not 'high').
    if zone is not None and dist is not None and dist > COVERAGE_KM:
        return "out", zone, dist, None
    # Override the summary risk with the risk at the selected horizon.
    return _risk_at_horizon(fc), zone, dist, fc


def _risk_at_horizon(fc: dict | None) -> str:
    """Return the risk level for the globally-selected forecast horizon.

    SEL_HORIZON is set in the sidebar: None = summary (worst across all
    horizons), or an int (0/24/48/72) for a specific forecast checkpoint.
    Falls back to the stored summary risk if horizon data is unavailable.
    """
    if not fc:
        return "none"
    horizon = globals().get("SEL_HORIZON")
    if horizon is None:
        return fc.get("risk_level", "none")
    for h in (fc.get("horizons") or []):
        if h.get("horizon_hours") == horizon:
            return h.get("risk_level", "none")
    return fc.get("risk_level", "none")


def _beach_risk_dated(beach: dict):
    """Date-aware risk that blends the physics forecast with seasonal climatology.

    Returns (risk_level, near_zone, dist_km, forecast, mode) where mode is:
      'physics'  — deterministic drift forecast (visit date within 3 days)
      'seasonal' — seasonal climatology estimate (date further out)
      'out'      — beach outside the monitored area (no physics coverage)
      None       — no zones / no data available

    The split point is the ~72h skill limit of the drift model: within that
    window we trust the physics forecast; beyond it we fall back to the
    Caribbean monthly climatology for the beach's coast.
    """
    import datetime as _d

    visit = globals().get("sel_date")
    days_ahead = (visit - _d.date.today()).days if visit is not None else 0

    # No date, or a near-term date → use the live drift forecast (existing path).
    if visit is None or 0 <= days_ahead <= 3:
        lvl, zone, dist, fc = _beach_risk(beach)
        if lvl is None:
            return None, None, None, None, None
        return lvl, zone, dist, fc, ("out" if lvl == "out" else "physics")

    # Far-future (or past) date → seasonal climatology for that month + coast.
    risk = seasonal_risk(visit.month, beach.get("region"))
    return risk, None, None, None, "seasonal"


def _fmt_arrival(fc: dict | None) -> str:
    """Format a forecast's estimated arrival as 'DD Mon HH:MM AST' or '' if unknown."""
    if not fc or not fc.get("eta_timestamp"):
        return ""
    try:
        import datetime as __dt
        _ts = __dt.datetime.fromisoformat(fc["eta_timestamp"].replace("Z", "+00:00"))
        _ts_ast = _ts.astimezone(__dt.timezone(__dt.timedelta(hours=-4)))
        return _ts_ast.strftime("%d %b %H:%M") + " AST"
    except Exception:
        return str(fc.get("eta_timestamp", ""))[:16]



# ---------------------------------------------------------------------------
# Sidebar — filters
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown(f"### {L['filters_header']}")
    sel_regions = st.multiselect(L["region"], all_regions(), default=all_regions())

    # Province options are restricted to provinces that exist within the
    # selected regions so the two filters stay in sync. Any previously-selected
    # province that no longer belongs to the current regions is silently dropped.
    _available_provinces = provinces_for_regions(sel_regions)
    _prev_provinces = st.session_state.get("sel_provinces_prev", [])
    _valid_prev = [p for p in _prev_provinces if p in _available_provinces]
    sel_provinces = st.multiselect(
        L["province"], _available_provinces, default=_valid_prev, key="province_filter"
    )
    st.session_state["sel_provinces_prev"] = sel_provinces
    sel_activities = st.multiselect(L["activity"], all_activities(), default=[])
    protected_only = st.checkbox(L["protected_only"], value=False)
    free_only = st.checkbox(L["free_only"], value=False)

    st.markdown("---")
    show_zones = st.checkbox(L["show_zones"], value=bool(zones), key="show_zones")
    show_masses = st.checkbox(L["show_masses"], value=True, key="show_masses")
    if not zones:
        st.caption(f"🌊 {L['risk_unavail']}")

    # Forecast horizon — the physics-limited time dimension of the prediction.
    # Sargassum drift is only reliable ~72h out, so we expose Now/+24/+48/+72h.
    if zones:
        _HORIZON_OPTS = {
            L["horizon_now"]: 0,
            "+24h": 24,
            "+48h": 48,
            "+72h": 72,
        }
        _h_label = st.radio(
            L["horizon"],
            options=list(_HORIZON_OPTS.keys()),
            index=0,
            horizontal=True,
            key="forecast_horizon",
            help=L["horizon_note"],
        )
        SEL_HORIZON: int | None = _HORIZON_OPTS[_h_label]
    else:
        SEL_HORIZON = None
    st.caption(L["season_note"])

    # Prediction methodology explainer
    with st.expander(L["prediction_info_title"], expanded=False):
        st.markdown(
            f"<div style='font-size:11px;line-height:1.5;color:#b2ebf2'>"
            f"<div style='margin-bottom:6px'><strong style='color:#4dd0e1'>🔬 {L['prediction_physics']}</strong></div>"
            f"<div style='margin-bottom:6px'><strong style='color:#4dd0e1'>📊 {L['prediction_seasonal']}</strong></div>"
            f"<div style='margin-top:8px;padding:6px;background:rgba(255,193,7,.15);border-left:3px solid #ffc107;color:#fff3cd'>"
            f"{L['prediction_date_note']}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    
    st.markdown("---")
    # Date-of-visit picker — drives the time dimension of the sargassum
    # prediction AND filters beaches by best visiting season.
    #   • within 3 days  → physics drift forecast (high confidence)
    #   • further out    → seasonal climatology estimate (labelled clearly)
    # Leaving blank shows the live 'now' view for all 56 beaches.
    import datetime as _dt
    _today = _dt.date.today()
    sel_date = st.date_input(
        L["month_filter"],
        value=None,
        min_value=_dt.date(_today.year, 1, 1),
        max_value=_dt.date(_today.year, 12, 31),
        key="visit_date",
        help=L["month_note"] + " " + L["prediction_date_note"],
    )
    # None = no filter; otherwise filter by the month of the selected date
    sel_month: int | None = sel_date.month if sel_date else None
    # Tell the user which prediction method applies to the chosen date.
    if sel_date is not None:
        _days_ahead = (sel_date - _today).days
        if 0 <= _days_ahead <= 3:
            st.caption("🔬 " + L["method_physics"])
        else:
            st.caption("📊 " + L["method_seasonal"])


def _matches(beach: dict) -> bool:
    if sel_regions and beach["region"] not in sel_regions:
        return False
    if sel_provinces and beach["province"] not in sel_provinces:
        return False
    if sel_activities and not set(sel_activities).intersection(set(beach["activities"])):
        return False
    if protected_only and not beach["protected_area"]:
        return False
    if free_only and "free" not in beach["entrance_fee"].lower():
        return False
    if sel_month is not None and not beach_good_in_month(beach, sel_month):
        return False
    return True


filtered = [b for b in BEACHES if _matches(b)]
filtered_names = {b["name"] for b in filtered}

with st.sidebar:
    st.caption(L["results"].format(n=len(filtered), total=len(BEACHES)))
    st.markdown(
        "<div style='margin-top:18px;padding:10px 16px 8px;"
        "border-top:1px solid rgba(255,255,255,.18);text-align:center'>"
        "<span style='font-size:13px;color:#9edde6'>Made with 🌊 by </span>"
        "<a href='https://www.linkedin.com/in/ayesha-yege/' target='_blank' "
        "style='font-size:13px;font-weight:800;color:#4dd0e1;text-decoration:none'>"
        "Ayesha Yege ↗</a>"
        "</div>",
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# Build Folium map
# ---------------------------------------------------------------------------
m = folium.Map(location=[19.0, -69.8], zoom_start=7, tiles="CartoDB Voyager")
cluster = MarkerCluster(
    options={"maxClusterRadius": 50, "disableClusteringAtZoom": 11}
).add_to(m)

for b in filtered:
    # Beach markers are WHITE pins with a COLORED RING (region color).
    # This visually separates beaches from risk zones (filled colored rectangles).
    region_color = REGION_COLORS.get(b["region"], "#1f77b4")
    risk_level, near_zone, dist_km_b, _fc_b, _mode_b = _beach_risk_dated(b)
    turtle_icon = " 🐢" if b["protected_area"] else ""
    desc_short = (b["description"] or "")[:170].rstrip()
    if len(b["description"] or "") > 170:
        desc_short += "…"

    # Risk badge — always present
    if _mode_b == "seasonal" and risk_level is not None:
        rc = RISK_COLORS.get(risk_level, "#6c757d")
        label_txt = RISK_LABEL.get(risk_level, risk_level.upper())
        risk_badge = (
            f"<div style='background:{rc};color:#fff;display:inline-block;"
            f"padding:3px 12px;border-radius:20px;font-size:11px;font-weight:700;margin:5px 0'>"
            f"🌊 Sargazo: {label_txt}"
            f"</div>"
            f"<div style='color:#b2ebf2;font-size:10.5px;margin-top:4px'>"
            f"📊 {L['seasonal_badge']}</div>"
        )
    elif risk_level is not None and near_zone:
        rc = RISK_COLORS.get(risk_level, "#6c757d")
        label_txt = RISK_LABEL.get(risk_level, risk_level.upper())
        _arrival = _fmt_arrival(_fc_b)
        _arrival_line = (
            f"<div style='color:#b2ebf2;font-size:10.5px;margin-top:4px'>"
            f"📅 Llegada estimada / ETA: {_arrival}</div>"
            if _arrival else ""
        )
        risk_badge = (
            f"<div style='background:{rc};color:#fff;display:inline-block;"
            f"padding:3px 12px;border-radius:20px;font-size:11px;font-weight:700;margin:5px 0'>"
            f"🌊 Sargazo: {label_txt} · {near_zone['name']} (~{dist_km_b:.0f} km)"
            f"</div>{_arrival_line}"
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
    # Draw beach as a white pin with a colored ring (region color).
    # This makes beaches visually distinct from risk zones (colored filled rectangles).
    folium.CircleMarker(
        location=[b["latitude"], b["longitude"]],
        radius=9,
        color=region_color,
        fill=True,
        fill_color="#ffffff",
        fill_opacity=1.0,
        weight=3,
        popup=folium.Popup(popup_html, max_width=340),
        tooltip=f"🏖️ {b['name']} — {b['province']}",
    ).add_to(cluster)

# ---------------------------------------------------------------------------
# Sargassum monitoring zones — drawn as translucent rectangles when toggled
# ---------------------------------------------------------------------------
_ZONE_BOX_DEG = 0.5  # half-width, must match pipeline/config.py ZONE_BOX_HALF_DEG (~55 km)

if show_zones and zones:
    for _z in zones:
        _zid = _z["id"]
        _fc_z = forecast_by_zone_id.get(_zid)
        _z_risk = _risk_at_horizon(_fc_z) if _fc_z else "none"
        _z_color = RISK_COLORS.get(_z_risk, "#6c757d")
        _z_label = RISK_LABEL.get(_z_risk, _z_risk.upper())
        _clat = _z["center_lat"]
        _clon = _z["center_lon"]

        # Draw bounding box rectangle
        folium.Rectangle(
            bounds=[
                [_clat - _ZONE_BOX_DEG, _clon - _ZONE_BOX_DEG],
                [_clat + _ZONE_BOX_DEG, _clon + _ZONE_BOX_DEG],
            ],
            color=_z_color,
            fill=True,
            fill_color=_z_color,
            fill_opacity=0.18,
            weight=2,
            dash_array="6 4",
            popup=folium.Popup(
                f"<div style='font-family:Nunito,sans-serif;min-width:180px;"
                f"background:linear-gradient(135deg,#003540,#005f6e);border-radius:12px;"
                f"padding:12px 14px;color:#e0f7fa'>"
                f"<div style='font-size:14px;font-weight:900;color:#fff;margin-bottom:4px'>"
                f"🌊 {_z['name']}</div>"
                f"<div style='background:{_z_color};border-radius:16px;display:inline-block;"
                f"padding:3px 12px;font-size:11px;font-weight:800;color:#fff;margin-bottom:6px'>"
                f"{_z_label}</div>"
                + (
                    f"<div style='color:#b2dfdb;font-size:11px'>⏱️ ETA ~{_fc_z['eta_hours']}h</div>"
                    if _fc_z and _fc_z.get("eta_hours") is not None else ""
                )
                + (
                    f"<div style='color:#b2dfdb;font-size:11px'>📅 {_fmt_arrival(_fc_z)}</div>"
                    if _fmt_arrival(_fc_z) else ""
                )
                + (
                    f"<div style='color:#80cbc4;font-size:10px;margin-top:4px'>"
                    f"🔄 {_fc_z.get('run_at','')[:16].replace('T',' ')} UTC</div>"
                    if _fc_z and _fc_z.get("run_at") else ""
                )
                + f"</div>",
                max_width=220,
            ),
            tooltip=f"🌊 {_z['name']} — {_z_label}",
        ).add_to(m)

        # Zone centre pin
        folium.CircleMarker(
            location=[_clat, _clon],
            radius=5,
            color=_z_color,
            fill=True,
            fill_color=_z_color,
            fill_opacity=1.0,
            weight=2,
        ).add_to(m)

# ---------------------------------------------------------------------------
# Detected sargassum masses — plotted as brown blobs sized by area.
# ---------------------------------------------------------------------------
if show_masses:
    _masses = _cached_fetch_detections(API_BASE_URL)
    if _masses:
        # Cap to the largest masses by area. Rendering thousands of individual
        # markers bloats the page HTML and slows the map; the biggest masses
        # are what matter visually, so we keep only the top N by area.
        _MAX_MASS_MARKERS = 350
        if len(_masses) > _MAX_MASS_MARKERS:
            _masses = sorted(
                _masses,
                key=lambda d: float(d.get("area_km2", 0.0) or 0.0),
                reverse=True,
            )[:_MAX_MASS_MARKERS]
        _mass_group = folium.FeatureGroup(name="Sargazo", show=True)
        for _d in _masses:
            try:
                _a = float(_d.get("area_km2", 0.0))
            except (TypeError, ValueError):
                _a = 0.0
            # Radius scales with area (sqrt so big masses don't dominate); clamp 3–18 px.
            _r = max(3.0, min(18.0, 3.0 + (_a ** 0.5) * 4.0))
            folium.CircleMarker(
                location=[_d["lat"], _d["lon"]],
                radius=_r,
                color="#5d4037",
                fill=True,
                fill_color="#795548",
                fill_opacity=0.55,
                weight=1,
                tooltip=f"🟤 Sargazo · {_a:.2f} km²",
            ).add_to(_mass_group)
        _mass_group.add_to(m)
    # If no masses came back we silently skip — sidebar caption explains why below.

# Floating legend — split into two sections to avoid confusion:
# 1) Beach pins (white + colored ring = region)
# 2) Risk zones (filled colored rectangles = sargassum risk)
beach_legend_rows = "".join(
    f"<div style='display:flex;align-items:center;gap:7px;margin:2px 0'>"
    f"<div style='width:13px;height:13px;border-radius:50%;background:#fff;"
    f"border:2.5px solid {c};flex-shrink:0'></div>"
    f"<span style='font-size:11px;color:#37474f'>{r}</span></div>"
    for r, c in REGION_COLORS.items()
)

if zones:
    risk_legend_rows = "".join(
        f"<div style='display:flex;align-items:center;gap:7px;margin:2px 0'>" 
        f"<div style='width:16px;height:10px;background:{c};flex-shrink:0;border-radius:2px'></div>"
        f"<span style='font-size:11px;color:#37474f'>{RISK_LABEL.get(lv, lv)}</span></div>"
        for lv, c in RISK_COLORS.items()
    )
    legend_html = (
        f"<div style='font-weight:800;font-size:11.5px;color:#005f73;margin-bottom:3px'>"
        f"{L['beach_legend']}</div>{beach_legend_rows}"
        f"<div style='border-top:1px solid #ccc;margin:6px 0'></div>"
        f"<div style='font-weight:800;font-size:11.5px;color:#005f73;margin-bottom:3px'>"
        f"{L['zone_legend']}</div>{risk_legend_rows}"
    )
else:
    legend_html = (
        f"<div style='font-weight:800;font-size:11.5px;color:#005f73;margin-bottom:3px'>"
        f"{L['beach_legend']}</div>{beach_legend_rows}"
    )

m.get_root().html.add_child(folium.Element(
    f"<div style='position:absolute;top:10px;left:60px;z-index:1000;"
    f"background:rgba(0,96,100,.88);color:#fff;border-radius:10px;"
    f"padding:8px 14px;font-size:12px;font-weight:700;backdrop-filter:blur(4px)'>"
    f"{L['tip']}</div>"
))

# Render map — stable key preserves pan/zoom between beach selections
map_result = st_folium(
    m,
    width="100%",
    height=900,
    returned_objects=["last_object_clicked_tooltip"],
    key="beach_map",
)

# Sync map click → session state (skip rerun if already selected)
if map_result and map_result.get("last_object_clicked_tooltip"):
    tooltip_text: str = map_result["last_object_clicked_tooltip"]
    clicked = tooltip_text.replace("🏖️ ", "").split(" — ")[0].strip()
    if clicked in filtered_names and clicked != st.session_state.get("selected_beach"):
        st.session_state["selected_beach"] = clicked
        st.rerun()

# ---------------------------------------------------------------------------
# Legend — rendered in the Streamlit DOM (position:fixed) so it is NEVER
# clipped by the Folium iframe viewport (bottom-left, always visible).
# ---------------------------------------------------------------------------
st.markdown(
    f"<div class='map-legend'>{legend_html}</div>",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Floating detail bubble — position:fixed in Streamlit DOM (hovers over map)
# ---------------------------------------------------------------------------
_sel_name = st.session_state.get("selected_beach")
if filtered:
    _sel_set = {b["name"] for b in filtered}
    if _sel_name not in _sel_set:
        _sel_name = sorted(filtered, key=lambda x: x["name"])[0]["name"]
    _panel_beach: dict | None = next(b for b in filtered if b["name"] == _sel_name)
else:
    _panel_beach = None

if _panel_beach:
    _pb = _panel_beach
    _risk_level, _near_zone, _dist_km, _fc, _mode = _beach_risk_dated(_pb)
    _turtle = " 🐢" if _pb["protected_area"] else ""

    # ── Advisory text per risk level ──────────────────────────────────────
    _ADVISORY = {
        "none":   ("🟢", "Sin sargazo detectado en la zona más cercana.",
                         "No sargassum detected in the nearest monitored zone."),
        "low":    ("🟡", "Bajo riesgo. Posibles trazas en playa. Condiciones normales.",
                         "Low risk. Possible traces on shore. Normal conditions."),
        "medium": ("🟠", "Riesgo medio. Sargazo esperado. Puede afectar el agua.",
                         "Medium risk. Sargassum incoming. Water may be affected."),
        "high":   ("🔴", "ALTO RIESGO. Llegada inminente. Planifica con antelación.",
                         "HIGH RISK. Arrival imminent. Plan your visit accordingly."),
        "out":    ("⚪", "Fuera del área de monitoreo. Sin datos de sargazo para esta playa.",
                         "Outside the monitored area. No sargassum data for this beach."),
    }

    # ── Build the sargassum section ───────────────────────────────────────
    if _mode == "seasonal" and _risk_level:
        # Seasonal climatology estimate (far-future date). No zone/ETA — this is
        # a statistical expectation for the month, not a deterministic forecast.
        _rc = RISK_COLORS.get(_risk_level, "#607d8b")
        _rlbl = RISK_LABEL.get(_risk_level, _risk_level.upper())
        _emoji, _advice_es, _advice_en = _ADVISORY.get(_risk_level, ("⚪", "", ""))
        _advice = _advice_en if lang == "en" else _advice_es
        _seasonal_note = L["seasonal_advisory"]
        _risk_section = (
            f"<div style='background:rgba(0,0,0,.25);border-radius:12px;"
            f"padding:10px 12px;margin:9px 0;border-left:4px solid {_rc}'>"
            f"<div style='display:flex;align-items:center;gap:8px;margin-bottom:6px'>"
            f"<span style='background:{_rc};color:#fff;border-radius:16px;"
            f"padding:2px 10px;font-size:11px;font-weight:800'>{_emoji} {_rlbl}</span>"
            f"<span style='background:rgba(255,193,7,.22);color:#ffe082;border-radius:16px;"
            f"padding:2px 10px;font-size:10px;font-weight:700'>📊 {L['seasonal_badge']}</span>"
            f"</div>"
            f"<div style='color:#cfd8dc;font-size:11.5px;line-height:1.45;"
            f"margin-bottom:6px'>{_advice}</div>"
            f"<div style='color:#ffcc80;font-size:10.5px;line-height:1.4;"
            f"margin-bottom:4px'>⚠️ {_seasonal_note}</div>"
            f"<div style='color:#80cbc4;font-size:10px;border-top:1px solid "
            f"rgba(255,255,255,.1);padding-top:5px;margin-top:5px'>"
            f"{L['method_seasonal']}</div>"
            f"</div>"
        )
    elif _risk_level and _near_zone:
        _rc = RISK_COLORS.get(_risk_level, "#607d8b")
        _rlbl = RISK_LABEL.get(_risk_level, _risk_level.upper())
        _emoji, _advice_es, _advice_en = _ADVISORY.get(_risk_level, ("⚪", "", ""))
        _advice = _advice_en if lang == "en" else _advice_es

        # ETA line
        _eta_line = ""
        if _fc:
            _eta_h = _fc.get("eta_hours")
            _eta_ts = _fc.get("eta_timestamp")
            _run_at = _fc.get("run_at", "")
            if _eta_h is not None:
                _eta_line += (
                    f"<div style='display:flex;justify-content:space-between;"
                    f"align-items:center;margin:5px 0'>"
                    f"<span style='color:#80cbc4;font-size:10px;font-weight:700;"
                    f"text-transform:uppercase;letter-spacing:.5px'>⏱️ ETA</span>"
                    f"<span style='color:#e0f7fa;font-size:12px;font-weight:700'>"
                    f"~{_eta_h}h</span></div>"
                )
            if _eta_ts:
                # Format to readable local time (DR is UTC-4)
                try:
                    import datetime as _dt
                    _ts = _dt.datetime.fromisoformat(_eta_ts.replace("Z", "+00:00"))
                    _ts_dr = _ts.astimezone(_dt.timezone(
                        _dt.timedelta(hours=-4)))
                    _eta_fmt = _ts_dr.strftime("%d %b %H:%M")
                except Exception:
                    _eta_fmt = _eta_ts[:16]
                _eta_line += (
                    f"<div style='display:flex;justify-content:space-between;"
                    f"align-items:center;margin:3px 0'>"
                    f"<span style='color:#80cbc4;font-size:10px;font-weight:700;"
                    f"text-transform:uppercase;letter-spacing:.5px'>📅 Llegada / Arrival</span>"
                    f"<span style='color:#e0f7fa;font-size:12px'>{_eta_fmt} AST</span></div>"
                )
            if _run_at:
                try:
                    import datetime as _dt
                    _ru = _dt.datetime.fromisoformat(_run_at.replace("Z", "+00:00"))
                    _ru_dr = _ru.astimezone(_dt.timezone(_dt.timedelta(hours=-4)))
                    _ru_fmt = _ru_dr.strftime("%d %b %H:%M")
                except Exception:
                    _ru_fmt = _run_at[:16]
                _eta_line += (
                    f"<div style='display:flex;justify-content:space-between;"
                    f"align-items:center;margin:3px 0'>"
                    f"<span style='color:#546e7a;font-size:10px;letter-spacing:.4px'>"
                    f"🔄 Actualizado / Updated</span>"
                    f"<span style='color:#78909c;font-size:10px'>{_ru_fmt} AST</span></div>"
                )

        _risk_section = (
            f"<div style='background:rgba(0,0,0,.25);border-radius:12px;"
            f"padding:10px 12px;margin:9px 0;border-left:4px solid {_rc}'>"
            # Header row: badge + zone
            f"<div style='display:flex;align-items:center;gap:8px;margin-bottom:6px'>"
            f"<span style='background:{_rc};color:#fff;border-radius:16px;"
            f"padding:2px 10px;font-size:11px;font-weight:800'>{_emoji} {_rlbl}</span>"
            f"<span style='color:#80cbc4;font-size:11px'>{_near_zone['name']}"
            f" &nbsp;·&nbsp; ~{_dist_km:.0f} km</span></div>"
            # Advisory
            f"<div style='color:#cfd8dc;font-size:11.5px;line-height:1.45;"
            f"margin-bottom:6px'>{_advice}</div>"
            # ETA rows
            + _eta_line +
            f"<div style='color:#80cbc4;font-size:10px;border-top:1px solid "
            f"rgba(255,255,255,.1);padding-top:5px;margin-top:5px'>"
            f"{L['method_physics']}</div>"
            f"</div>"
        )
    else:
        _risk_section = (
            f"<div style='background:rgba(0,0,0,.2);border-radius:12px;"
            f"padding:10px 12px;margin:9px 0;border-left:4px solid #546e7a'>"
            f"<div style='color:#90a4ae;font-size:11.5px'>🌊 {L['risk_unavail']}</div>"
            f"</div>"
        )


    def _brow(icon_: str, label_: str, val_: str) -> str:
        if not val_:
            return ""
        return (
            f"<div style='margin:5px 0'>"
            f"<div style='color:#80cbc4;font-size:10px;font-weight:700;"
            f"text-transform:uppercase;letter-spacing:.5px'>{icon_} {label_}</div>"
            f"<div style='color:#e0f7fa;font-size:12px;line-height:1.4'>{val_}</div>"
            f"</div>"
        )

    _acts = ", ".join(_pb["activities"]) if _pb["activities"] else "N/A"
    _wild = ", ".join(_pb["wildlife"]) if _pb["wildlife"] else "N/A"
    _facs = ", ".join(_pb["facilities"]) if _pb["facilities"] else "N/A"
    _park = ("✅ " + L["yes"]) if _pb["parking"] else ("⚠️ " + L["no_limited"])
    _desc_raw = _pb.get("description") or ""
    _desc = _desc_raw[:200] + ("…" if len(_desc_raw) > 200 else "")

    _bubble = (
        "<div class='beach-detail'>"
        # Beach name + location
        f"<div style='font-size:17px;font-weight:900;color:#fff;line-height:1.2;margin-bottom:3px'>"
        f"{_pb['name']}{_turtle}</div>"
        f"<div style='font-size:11px;color:#80cbc4;margin-bottom:4px'>"
        f"📍 {_pb['province']} · {_pb['region']}</div>"
        # Sargassum risk section (rich card)
        + _risk_section +
        # Description
        f"<div style='font-size:11.5px;color:#b2dfdb;line-height:1.5;margin-bottom:11px;"
        f"border-left:3px solid rgba(0,255,180,.25);padding-left:9px'>{_desc}</div>"
        f"<hr style='border:none;border-top:1px solid rgba(255,255,255,.13);margin:9px 0'>"
        + _brow("🗓️", L["best_time"], _pb["best_time_to_visit"])
        + _brow("🎟️", L["entrance"], _pb["entrance_fee"])
        + _brow("🅿️", L["parking"], _park)
        + _brow("🌊", L["water"], _pb["water_conditions"])
        + _brow("🚪", L["access"], f"{_pb['access_type']} — {_pb['access_description']}")
        + "<hr style='border:none;border-top:1px solid rgba(255,255,255,.13);margin:9px 0'>"
        + _brow("🏄", L["activities"], _acts)
        + _brow("🐠", L["wildlife"], _wild)
        + _brow("🏗️", L["facilities"], _facs)
        + _brow("🌿", L["ecosystem"], _pb["ecosystem"])
        + f"<a href='{_pb['google_maps_url']}' target='_blank' style='"
        "display:block;text-align:center;margin-top:14px;"
        "background:linear-gradient(135deg,rgba(0,180,130,.4),rgba(0,120,100,.4));"
        "color:#fff;"
        "border:1px solid rgba(0,255,180,.3);border-radius:25px;"
        "padding:9px;font-size:12px;font-weight:800;text-decoration:none'>"
        f"📍 {L['open_maps']} ↗</a>"
        "</div>"
    )
    st.markdown(_bubble, unsafe_allow_html=True)


