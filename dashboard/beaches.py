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
    BEACHES,
)
from dashboard.risk_overlay import (
    RISK_COLORS,
    RISK_EMOJI,
    fetch_detections,
    fetch_live_risk,
    risk_for_beach,
    haversine_km,
    risk_from_detections,
)
from dashboard.climatology import seasonal_index, seasonal_risk

load_dotenv()

API_BASE_URL = os.environ.get("API_BASE_URL", "https://api.descubreplayas.com.do")
GA_MEASUREMENT_ID = os.environ.get("GA_MEASUREMENT_ID", "")

st.set_page_config(
    page_title="Descubre Playas RD",
    page_icon="🌴",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Google Analytics 4 — inject gtag.js only when a Measurement ID is set.
# Set GA_MEASUREMENT_ID=G-XXXXXXXXXX in the Cloud Run environment or .env.
# ---------------------------------------------------------------------------
if GA_MEASUREMENT_ID:
    st.markdown(
        f"""
<script async src="https://www.googletagmanager.com/gtag/js?id={GA_MEASUREMENT_ID}"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());
  gtag('config', '{GA_MEASUREMENT_ID}', {{
    page_title: document.title,
    page_location: window.location.href
  }});
</script>
""",
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# SEO — meta tags, Open Graph, JSON-LD structured data
# 
# PRODUCTION (Docker): docker-entrypoint-dashboard.sh patches Streamlit's
# index.html template at container startup, injecting canonical + core meta
# tags into the server-rendered HTML so Googlebot sees them immediately.
#
# DEV / FALLBACK: The JavaScript injection below adds the same tags to 
# document.head after React hydrates. This ensures local `streamlit run` 
# sessions also have SEO tags (though Google may not see them on first crawl).
# ---------------------------------------------------------------------------
import json as _json_mod

_APP_URL = os.environ.get("APP_CANONICAL_URL", "https://descubreplayas.com.do")
_DESC_ES = (
    "Guía de 56 playas de República Dominicana con alertas de sargazo en tiempo "
    "real, riesgo por playa, pronóstico de llegada, actividades y acceso."
)
_DESC_EN = (
    "Guide to 56 Dominican Republic beaches with live sargassum risk forecast, "
    "activities, access info and early-warning sargassum alerts."
)
_KEYWORDS = (
    "playas República Dominicana, sargazo RD, alerta sargazo, Dominican Republic "
    "beaches, sargassum alert, Punta Cana, Samáná, Puerto Plata, Barahona, "
    "Bahía de las Águilas, Playa Rincón, ecoturismo dominicano, DR beaches"
)


def _slugify(name: str) -> str:
    import unicodedata as _ud

    n = _ud.normalize("NFKD", name)
    n = n.encode("ascii", "ignore").decode("ascii")
    n = n.lower()
    out = []
    for ch in n:
        if ch.isalnum():
            out.append(ch)
        else:
            out.append("-")
    s = "".join(out)
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")

# JSON-LD built as a Python dict so its curly braces never conflict with
# f-string syntax. Injected in a separate st.markdown() call.
_json_ld = {
    "@context": "https://schema.org",
    "@graph": [
        {
            "@type": "WebApplication",
            "name": "Descubre Playas RD",
            "url": _APP_URL,
            "description": _DESC_ES,
            "applicationCategory": "TravelApplication",
            "operatingSystem": "Web Browser",
            "inLanguage": ["es-DO", "en"],
            "author": {
                "@type": "Person",
                "name": "Ayesha Yege",
                "url": "https://www.linkedin.com/in/ayesha-yege/",
            },
            "about": {
                "@type": "Place",
                "name": "República Dominicana",
                "containedInPlace": {"@type": "Country", "name": "Dominican Republic"},
            },
            "featureList": [
                "Alertas de sargazo en tiempo real — 56 playas RD",
                "Pronóstico de llegada por playa (72 h física + ML extendido)",
                "Filtros por región, provincia, actividad y riesgo",
                "Masas de sargazo detectadas vía satélite Sentinel-2",
                "Bilingüe español / inglés",
            ],
            "keywords": _KEYWORDS,
        },
        {
            "@type": "ItemList",
            "name": "Playas de República Dominicana",
            "description": _DESC_ES,
            "numberOfItems": len(BEACHES),
            "itemListElement": [],
        },
    ],
}
# Populate ItemList from BEACHES using pretty /beach/<slug> URLs
try:
    items = []
    for i, b in enumerate(BEACHES):
        name = b.get("name")
        if not name:
            continue
        slug = _slugify(name)
        url = _APP_URL.rstrip("/") + "/beach/" + slug
        items.append({"@type": "ListItem", "position": i + 1, "name": name, "url": url})
    _json_ld["@graph"][-1]["itemListElement"] = items
    _json_ld["@graph"][-1]["numberOfItems"] = len(items)
except Exception:
    pass

_json_ld_str = _json_mod.dumps(_json_ld, ensure_ascii=False)

# Meta tags injected synchronously into <head> so Googlebot sees them immediately.
# The script creates DOM elements and appends them to document.head before any
# Streamlit rendering. This ensures canonical + OG tags are present in the
# snapshot Google takes during its crawl (even before JS hydration completes).
st.markdown(
    f"""<script>
(function(){{
  var head = document.head;
  function addMeta(name, content, prop) {{
    var meta = document.createElement('meta');
    if (prop) meta.setAttribute('property', prop);
    else if (name) meta.setAttribute('name', name);
    if (content) meta.setAttribute('content', content);
    head.appendChild(meta);
  }}
  function addLink(rel, href) {{
    var link = document.createElement('link');
    link.setAttribute('rel', rel);
    link.setAttribute('href', href);
    head.appendChild(link);
  }}
  
  // Language & geo
  document.documentElement.setAttribute('lang', 'es-DO');
  addMeta('content-language', null); 
  head.appendChild(Object.assign(document.createElement('meta'), {{
    httpEquiv: 'content-language', content: 'es-DO'
  }}));
  
  // Core SEO
  addMeta('description', "{_DESC_ES} {_DESC_EN}");
  addMeta('keywords', "{_KEYWORDS}");
  addMeta('robots', 'index, follow, max-snippet:-1, max-image-preview:large');
  addMeta('author', 'Ayesha Yege');
  addMeta('geo.region', 'DO');
  addMeta('geo.placename', 'República Dominicana');
  
  // CANONICAL — critical for Google Search Console
  addLink('canonical', '{_APP_URL}');
  
  // Open Graph
  addMeta(null, 'website', 'og:type');
  addMeta(null, '{_APP_URL}', 'og:url');
  addMeta(null, 'Descubre Playas RD 🌴 — 56 Playas + Alertas de Sargazo', 'og:title');
  addMeta(null, "{_DESC_ES}", 'og:description');
  addMeta(null, 'es_DO', 'og:locale');
  addMeta(null, 'en_US', 'og:locale:alternate');
  addMeta(null, 'Descubre Playas RD', 'og:site_name');
  
  // Twitter Card
  addMeta('twitter:card', 'summary_large_image');
  addMeta('twitter:title', 'Descubre Playas RD — Alertas Sargazo Tiempo Real');
  addMeta('twitter:description', "{_DESC_ES}");
}})();
</script>""",
    unsafe_allow_html=True,
)
# JSON-LD in its own script tag (already valid JSON string from _json_ld_str above)
st.markdown(
    "<script type='application/ld+json'>" + _json_ld_str + "</script>",
    unsafe_allow_html=True,
)

# If the page was loaded with `?beach=Name`, restore a pretty path after
# server-side processing so the visible URL is `/beach/<slug>` while the app
# has been rendered using the query param. This keeps both server indexing
# and user-visible pretty URLs working.
st.markdown(
        """
<script>
    (function(){
        try {
            var params = new URLSearchParams(window.location.search);
            var beach = params.get('beach');
            if (beach && !window.location.pathname.startsWith('/beach/')) {
                // slugify in JS: ASCII-fallback, lower, replace non-alnum with '-'
                var s = beach.normalize('NFKD').replace(/\p{Diacritic}/gu, '');
                s = s.toLowerCase();
                s = s.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
                var pretty = '/beach/' + encodeURIComponent(s);
                // Replace history so navigation stays on pretty path without reload
                window.history.replaceState({}, document.title, pretty + window.location.hash);
            }
        } catch(e) { /* noop */ }
    })();
</script>
""",
        unsafe_allow_html=True,
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
        "risk_filter": "🌊 Filtrar por riesgo de sargazo",
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
        "prediction_ml": "ML (4-21d): Modelo supervisado entrenado en datos históricos. Más preciso que solo climatología.",
        "prediction_seasonal": "Temporada (>21d): Climatología histórica del Caribe. No predice eventos específicos.",
        "prediction_date_note": "⚠️ Hasta 3 días: pronóstico físico. 4-21 días: estimación ML. Más lejos: climatología estacional.",
        "ml_badge": "Pronóstico ML (extendido)",
        "method_ml": "Método: ML extendido · Confianza {conf}%",
        "ml_advisory": "Estimación basada en patrones históricos y condiciones actuales. Más precisa que climatología estacional, menos que física de 72h.",
        "ml_confidence": "Confianza modelo",
        "ml_note_map": "Vista extendida — ML · posiciones especulativas",
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
        "search_beach": "🔍 Buscar playa",
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
        "risk_filter": "🌊 Filter by sargassum risk",
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
        "prediction_ml": "ML (4-21d): Supervised model trained on historical data. More accurate than climatology alone.",
        "prediction_seasonal": "Seasonal (>21d): Caribbean historical climatology. Does not predict specific events.",
        "prediction_date_note": "⚠️ Within 3 days: physics drift. 4-21 days: ML estimate. Further: seasonal climatology.",
        "ml_badge": "ML Forecast (extended)",
        "method_ml": "Method: ML extended · Confidence {conf}%",
        "ml_advisory": "Estimate based on historical patterns and current conditions. More accurate than seasonal climatology, less than 72h physics.",
        "ml_confidence": "Model confidence",
        "ml_note_map": "Extended view — ML · speculative positions",
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
        "search_beach": "🔍 Search beach",
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
# zone centre. We tie it to the pipeline's actual monitored box so the two can
# never drift apart: the coverage radius is the box's corner distance (the
# farthest a point can be and still sit inside the box). Beyond this the beach
# is genuinely outside the monitored area and must NOT inherit the zone's risk.
from pipeline import config as _pcfg  # noqa: E402  (sys.path set above)

_ZONE_HALF_DEG = _pcfg.ZONE_BOX_HALF_DEG
# Corner distance of the square box (deg → km), latitude-corrected at ~19°N (DR).
import math as _math  # noqa: E402

_COVERAGE_NS_KM = _ZONE_HALF_DEG * 111.32
_COVERAGE_EW_KM = _ZONE_HALF_DEG * 111.32 * _math.cos(_math.radians(19.0))
COVERAGE_KM = _math.hypot(_COVERAGE_NS_KM, _COVERAGE_EW_KM)  # ≈ 54 km at 0.35°

# ---------------------------------------------------------------------------
# CSS — responsive full-viewport map, tropical palette, clean sidebar
# ---------------------------------------------------------------------------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700;800;900&display=swap');

*, html, body, [class*="css"] { font-family: 'Nunito', sans-serif; box-sizing: border-box; }

/* ── Hide Streamlit chrome WITHOUT killing the sidebar toggle ──
   We hide the menu/toolbar/deploy/status widgets entirely, and turn the header
   bar into a transparent, zero-footprint strip that floats OVER the map (so
   there is no ugly grey bar and no white gap), while the sidebar open/close
   controls inside it stay fully visible and tappable on every screen. */
#MainMenu,
footer,
[data-testid="stDecoration"],
[data-testid="stDeployButton"],
[data-testid="stStatusWidget"] {
    display: none !important;
    visibility: hidden !important;
    height: 0 !important;
    min-height: 0 !important;
    overflow: hidden !important;
}

/* Toolbar: DO NOT display:none — it hosts the expand-sidebar button
   (stExpandSidebarButton), and a child of a display:none parent can never be
   shown. Instead strip the toolbar's footprint and let clicks pass through to
   the map; the unwanted chrome items above are hidden individually, and the
   expand button is re-shown + repositioned as our floating "Filtros" control. */
[data-testid="stToolbar"] {
    background: transparent !important;
    height: 0 !important;
    min-height: 0 !important;
    pointer-events: none !important;
}

/* ── Kill the Streamlit "Made with Streamlit" loading splash ── */
[data-testid="stSplashScreen"],
.stSplashScreen,
div[class*="splashScreen"],
div[class*="SplashScreen"] {
    display: none !important;
    visibility: hidden !important;
    opacity: 0 !important;
    pointer-events: none !important;
}

/* Header bar: transparent + floating so it never shows the default grey
   Streamlit chrome and never pushes a white gap above the map. We keep it in
   the DOM (NOT display:none) because the sidebar-reopen button lives inside it. */
header,
[data-testid="stHeader"] {
    background: transparent !important;
    position: absolute !important;
    top: 0 !important;
    left: 0 !important;
    right: 0 !important;
    height: 0 !important;
    min-height: 0 !important;
    box-shadow: none !important;
    pointer-events: none !important;   /* the thin strip is click-through to the map… */
    z-index: 999990 !important;
}

/* …but the sidebar open/collapse controls MUST stay visible and clickable. */
[data-testid="stSidebarCollapsedControl"],
[data-testid="collapsedControl"],
[data-testid="stSidebarCollapseButton"],
[data-testid="stExpandSidebarButton"],
[data-testid="stBaseButton-headerNoPadding"],
[data-testid="baseButton-headerNoPadding"],
header button {
    display: flex !important;
    visibility: visible !important;
    pointer-events: auto !important;
    opacity: 1 !important;
    z-index: 1000000 !important;
}

/* The floating "reopen sidebar" button (shown when the drawer is collapsed):
   make it an obvious, tappable teal pill above the map on every screen size.
   In Streamlit 1.58 this control is [data-testid="stExpandSidebarButton"]. */
[data-testid="stExpandSidebarButton"],
[data-testid="stSidebarCollapsedControl"],
[data-testid="collapsedControl"] {
    position: fixed !important;
    top: 10px !important;
    left: 10px !important;
    display: flex !important;
    align-items: center !important;
    background: linear-gradient(135deg,rgba(0,96,100,.97),rgba(0,130,140,.97)) !important;
    border-radius: 22px !important;
    padding: 7px 16px !important;
    box-shadow: 0 3px 14px rgba(0,0,0,.35), 0 0 0 1px rgba(255,255,255,.18) !important;
    backdrop-filter: blur(6px) !important;
}
/* Text label so the reveal button is self-explanatory on DESKTOP too (not just
   mobile). Shows next to the » icon whenever the sidebar is collapsed. */
[data-testid="stExpandSidebarButton"]::after,
[data-testid="stSidebarCollapsedControl"]::after,
[data-testid="collapsedControl"]::after {
    content: 'Filtros';
    color: #fff !important;
    font-family: 'Nunito', sans-serif;
    font-weight: 800;
    font-size: 13px;
    letter-spacing: .3px;
    margin-left: 6px;
    white-space: nowrap;
}
[data-testid="stExpandSidebarButton"] svg,
[data-testid="stSidebarCollapsedControl"] svg,
[data-testid="collapsedControl"] svg,
[data-testid="stExpandSidebarButton"] button,
[data-testid="stSidebarCollapsedControl"] button,
[data-testid="collapsedControl"] button {
    color: #fff !important;
    fill: #fff !important;
    pointer-events: auto !important;
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

/* ── Kill the flex GAP on the main content vertical block ──
   THIS is the real cause of the white bar at the top. Streamlit lays the main
   column out as a flex container with a ~1rem `gap` between every child. The
   page injects several invisible st.markdown() blocks (GA, meta, JSON-LD,
   pretty-URL, <style>, and the #sarg-loader div) BEFORE the map. Even when
   those children are 0px tall (scripts/styles are display:none by the UA, and
   the loader is position:fixed), each one still contributes ONE flex gap, and
   the stack of gaps adds up to a visible white band above the map.
   Setting gap:0 on the MAIN block removes that band. The sidebar lives in a
   separate block, so its spacing is unaffected. The only in-flow visual child
   here is the map iframe; the detail panel & legend are position:fixed. */
section[data-testid="stMain"] [data-testid="stVerticalBlock"],
[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlock"] {
    gap: 0 !important;
}

/* ── Collapse technical containers (script / JSON-LD / style injections) ──
   Streamlit renders every st.markdown() as an stElementContainer that still
   occupies a vertical-block gap even when its only content is an invisible
   <script> or <style> tag. That stacked gap is what pushes the map down and
   leaves the white band on top. display:none removes them from flex layout
   entirely (a hidden element contributes no gap), so the map hits the top.
   NOTE: the #sarg-loader block is a plain <div> (no script/style) so it is
   never matched here — and it is position:fixed anyway. */
[data-testid="stElementContainer"]:has(> [data-testid="stMarkdown"] script),
[data-testid="stElementContainer"]:has(> [data-testid="stMarkdown"] style),
[data-testid="stElementContainer"]:has(> [data-testid="stMarkdownContainer"] script),
[data-testid="stElementContainer"]:has(> [data-testid="stMarkdownContainer"] style),
.element-container:has(script[type="application/ld+json"]) {
    display: none !important;
}

/* ── App BG ── */
.stApp {
    background: #f0fafa;
    overflow-x: hidden;
    height: 100vh !important;
    max-height: 100vh !important;
    overflow-y: hidden !important;
}
html, body { height: 100vh; overflow: hidden; }

/* ── Map iframe: full viewport height on all screen sizes ──
   streamlit-folium renders the map as a CUSTOM COMPONENT iframe
   (data-testid="stCustomComponentV1"), NOT a plain stIFrame, so it must be
   targeted explicitly or it stays stuck at its fixed 900px height and leaves a
   background gap below the map on tall viewports. We also zero-out its wrapper
   so nothing adds extra height around it. The inner Leaflet map is forced to
   100% via a folium-side <style> injection (see m.get_root() below). */
.stIframe,
[data-testid="stIFrame"] iframe,
[data-testid="stCustomComponentV1"],
iframe[title="streamlit_folium.st_folium"] {
    height: calc(100vh - 4px) !important;
    min-height: 400px !important;
    width: 100% !important;
    border-radius: 0 !important;
    display: block !important;
}
/* The div wrapping the custom-component iframe must not reserve its own box. */
[data-testid="stElementContainer"]:has(> div > iframe[title="streamlit_folium.st_folium"]),
[data-testid="stElementContainer"]:has(> div > [data-testid="stCustomComponentV1"]) {
    height: calc(100vh - 4px) !important;
    line-height: 0 !important;
}
/* Streamlit's auto-resize anchor sits after the iframe and can add a sliver. */
[data-testid="stAppIframeResizerAnchor"] {
    display: none !important;
    height: 0 !important;
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
    -webkit-overflow-scrolling: touch; /* momentum scrolling on iOS */
    overscroll-behavior: contain;      /* prevent scroll chaining to body */
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
    .stIframe,
    [data-testid="stIFrame"] iframe,
    [data-testid="stCustomComponentV1"],
    iframe[title="streamlit_folium.st_folium"] {
        height: 57vh !important;
        min-height: 280px !important;
    }
    [data-testid="stElementContainer"]:has(> div > iframe[title="streamlit_folium.st_folium"]) {
        height: 57vh !important;
    }
    .risk-banner { font-size: 12px; padding: 7px 10px; }

    /* ── Filter drawer on mobile ──
       The sidebar becomes a fixed overlay drawer. We take FULL control of the
       hide/show with transform: Streamlit computes a negative margin from the
       sidebar's NATURAL width, but we override that width, so its own maths
       leaves a visible sliver at the edge. transform: translateX(-100%) on an
       element with an explicit width guarantees it slides fully off-screen.
       Default state is hidden; it slides in only when expanded. */
    [data-testid="stSidebar"] {
        position: fixed !important;
        top: 0 !important;
        left: 0 !important;
        height: 100vh !important;
        width: min(86vw, 360px) !important;
        min-width: min(86vw, 360px) !important;
        max-width: min(86vw, 360px) !important;
        z-index: 999995 !important;
        box-shadow: 6px 0 32px rgba(0,0,0,.45) !important;
        transform: translateX(-100%) !important;   /* hidden by default */
        transition: transform .3s ease !important;
    }
    /* Expanded state (Streamlit 1.58: the sidebar carries aria-expanded).
       aria-expanded="true"  → drawer open  → slide fully into view.
       aria-expanded="false" → collapsed     → base rule keeps it off-screen. */
    [data-testid="stSidebar"][aria-expanded="true"] {
        transform: translateX(0) !important;
    }
    /* Keep the sidebar's own collapse (×) button visible & tappable on mobile. */
    [data-testid="stSidebar"] [data-testid="stSidebarCollapseButton"],
    [data-testid="stSidebar"] button[kind="header"] {
        display: flex !important;
        visibility: visible !important;
        pointer-events: auto !important;
        opacity: 1 !important;
    }

    /* Detail panel → full-width bottom sheet.
       43vh gives ~260px of content on a 600px phone while 57vh map stays dominant.
       padding-top:22px clears the ::before drag-handle element. */
    .beach-detail {
        right: 0 !important; left: 0 !important;
        top: auto !important; bottom: 0 !important;
        width: 100% !important; max-width: 100% !important;
        max-height: 43vh !important;
        border-radius: 18px 18px 0 0 !important;
        padding: 22px 16px 20px !important;
        box-shadow: 0 -6px 32px rgba(0,0,0,.6) !important;
    }
    /* Drag-handle pill — positioned above the scrollable content so it never
       scrolls away. position:absolute works because .beach-detail is position:fixed
       (which creates a containing block for absolutely-positioned children). */
    .beach-detail::before {
        content: '';
        position: absolute; top: 8px; left: 50%;
        transform: translateX(-50%);
        width: 40px; height: 4px;
        background: rgba(255,255,255,.22);
        border-radius: 2px;
        pointer-events: none;
    }
    /* Legend → top-RIGHT; avoids the sidebar-reopen button (top-left) and
       stays well above the bottom sheet. */
    .map-legend {
        top: 54px !important; bottom: auto !important;
        left: auto !important; right: 8px !important; transform: none !important;
        max-height: 22vh !important; padding: 6px 10px !important;
        min-width: 0 !important; font-size: 11px;
    }
    /* ── Mobile FAB: replace the global small top-left button with a centered
       teal pill that floats above the bottom detail sheet.
       bottom:47vh sits 4vh clear above the 43vh-max panel.
       Streamlit 1.58 reveal control = stExpandSidebarButton. ── */
    [data-testid="stExpandSidebarButton"],
    [data-testid="stSidebarCollapsedControl"],
    [data-testid="collapsedControl"] {
        top: auto !important;
        left: 50% !important;
        right: auto !important;
        bottom: 47vh !important;
        transform: translateX(-50%) !important;
        background: linear-gradient(135deg,rgba(0,96,100,.97),rgba(0,130,140,.97)) !important;
        border-radius: 40px !important;
        padding: 11px 22px !important;
        box-shadow: 0 4px 22px rgba(0,0,0,.55), 0 0 0 1.5px rgba(255,255,255,.2) !important;
        backdrop-filter: blur(8px) !important;
        min-width: 132px !important;
    }
    /* Add a clear text label so the FAB obviously reveals the filters. */
    [data-testid="stExpandSidebarButton"]::after,
    [data-testid="stSidebarCollapsedControl"]::after,
    [data-testid="collapsedControl"]::after {
        content: 'Filtros';
        color: #fff !important;
        font-family: 'Nunito', sans-serif;
        font-weight: 800;
        font-size: 13px;
        letter-spacing: .3px;
        margin-left: 6px;
        white-space: nowrap;
    }
    /* Full-screen modal backdrop — shown only while the drawer is OPEN
       (aria-expanded="true" on the sidebar). pointer-events block interaction
       with the map behind the drawer so it feels modal. The drawer (z-index
       999995) stays above the backdrop (999994) so its ✕ close button remains
       tappable. Chrome 105+, Safari 15.4+, Firefox 121+. */
    body:has([data-testid="stSidebar"][aria-expanded="true"])::before {
        content: '';
        position: fixed; inset: 0;
        background: rgba(0,0,0,.58);
        z-index: 999994;
        pointer-events: auto;
    }
    /* Hide the reveal FAB while the drawer is open — no "open" button needed. */
    body:has([data-testid="stSidebar"][aria-expanded="true"]) [data-testid="stExpandSidebarButton"] {
        display: none !important;
    }
}

/* ── Very small screens (iPhone SE / Android compact) ── */
@media (max-width: 480px) {
    [data-testid="stSidebar"] h1 { font-size: 1rem !important; }
    .stIframe,
    [data-testid="stIFrame"] iframe,
    [data-testid="stCustomComponentV1"],
    iframe[title="streamlit_folium.st_folium"] {
        height: 54vh !important;
        min-height: 260px !important;
    }
    [data-testid="stElementContainer"]:has(> div > iframe[title="streamlit_folium.st_folium"]) {
        height: 54vh !important;
    }
    /* On 480px screens keep the panel at 42vh; the extra 2vh vs 768px slightly
       increases content visibility on the tallest compact phones (667px). */
    .beach-detail { max-height: 42vh !important; padding: 22px 14px 18px !important; }
    .map-legend { max-height: 18vh !important; font-size: 10px; }
}

/* ── Custom loading overlay — CSS-only auto-hide, no JS needed ──
   Streamlit strips/sandboxes injected <script> tags so we rely on
   animation-delay + fill-mode instead of a MutationObserver. The
   overlay fades out after ~8 s and pointer-events drop immediately
   so the map is never blocked even if the fade is still playing. */
#sarg-loader {
    position: fixed; inset: 0; z-index: 9999999;
    background: linear-gradient(160deg, #001f26 0%, #003540 40%, #005f6e 75%, #009dae 100%);
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    gap: 20px;
    /* fade out after 8 s, stay hidden afterwards */
    animation: sarg-loader-hide .5s ease 8s forwards;
}
/* pointer-events vanish as soon as the fade starts */
#sarg-loader { pointer-events: auto; }
@keyframes sarg-loader-hide {
    0%   { opacity: 1; pointer-events: auto;  }
    99%  { opacity: 0; pointer-events: none;  }
    100% { opacity: 0; visibility: hidden; pointer-events: none; }
}

#sarg-loader .sarg-wave {
    width: 64px; height: 64px;
    border: 5px solid rgba(255,255,255,.18);
    border-top-color: #4dd0e1;
    border-right-color: #00bcd4;
    border-radius: 50%;
    animation: sarg-spin .85s linear infinite;
}
@keyframes sarg-spin { to { transform: rotate(360deg); } }

#sarg-loader .sarg-label {
    color: #b2ebf2;
    font-family: 'Nunito', sans-serif;
    font-size: 15px; font-weight: 700;
    letter-spacing: .5px;
    animation: sarg-pulse 1.6s ease-in-out infinite;
}
@keyframes sarg-pulse {
    0%, 100% { opacity: .55; }
    50%       { opacity: 1;   }
}
#sarg-loader .sarg-brand {
    font-family: 'Nunito', sans-serif;
    font-size: 22px; font-weight: 900; color: #fff;
    letter-spacing: .5px; margin-bottom: 4px;
}</style>
""", unsafe_allow_html=True)

st.markdown("""
<div id="sarg-loader">
  <div class="sarg-brand">🌴 Descubre Playas RD</div>
  <div class="sarg-wave"></div>
  <div class="sarg-label">Cargando mapa… / Loading map…</div>
</div>
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
# Permalink — initialise selected_beach from ?beach=<name> query param so
# users can share direct links to a specific beach (e.g. from Telegram alerts).
# Only runs when selected_beach is not yet set to avoid overriding an in-session
# map click.
# ---------------------------------------------------------------------------
_qp = st.query_params
if "beach" in _qp and not st.session_state.get("selected_beach"):
    _beach_from_url = _qp.get("beach", "")
    if any(b["name"] == _beach_from_url for b in BEACHES):
        st.session_state["selected_beach"] = _beach_from_url

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


@st.cache_data(ttl=300)
def _cached_fetch_ml_forecasts(url: str) -> list[dict]:
    """Fetch ML extended forecasts (7/14/21-day) from the API."""
    if not url:
        return []
    try:
        import requests, certifi
        resp = requests.get(
            f"{url}/forecast/extended",
            timeout=15,
            verify=certifi.where(),
        )
        if resp.status_code == 200:
            return resp.json()
        return []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Live risk fetch
# ---------------------------------------------------------------------------
zones: list[dict] = []
risk_by_zone_id: dict[int, str] = {}
zones, forecast_by_zone_id = _cached_fetch_live_risk(API_BASE_URL)

# ML extended forecasts — fetched once per 5-min cache window
_ml_forecasts: list[dict] = _cached_fetch_ml_forecasts(API_BASE_URL)

# Pre-index ML forecasts by zone_id for O(1) lookups in _ml_risk_for_zone.
_ml_forecasts_by_zone_id: dict[int, list[dict]] = {}
for _f in _ml_forecasts:
    _zid = _f.get("zone_id")
    if _zid is not None:
        _ml_forecasts_by_zone_id.setdefault(int(_zid), []).append(_f)

# Fetch live 10 m wind once per session-hour and store as a module-level
# tuple so _beach_risk / _beach_eta_quick / _predict_position can all share
# the same value without re-hitting the API on every beach calculation.


def _ml_risk_for_zone(
    zone_id: int, days_ahead: int, ml_forecasts: list[dict]
) -> tuple[str, float, str] | None:
    """Return (risk_level, confidence, method) from ML forecasts for this zone.

    Picks the lead-day option (7/14/21) closest to days_ahead.
    Returns None if no ML forecast exists for the zone.
    """
    # O(1) lookup via pre-indexed dict; fall back to scanning the passed list
    # when the index isn't available (e.g. during unit tests).
    candidates = _ml_forecasts_by_zone_id.get(zone_id) or [
        f for f in ml_forecasts if f.get("zone_id") == zone_id
    ]
    if not candidates:
        return None
    best = min(candidates, key=lambda f: abs(f.get("lead_days", 7) - days_ahead))
    return best["risk_level"], float(best.get("confidence", 0.4)), best.get("method", "seasonal")


def _beach_risk(beach: dict):
    """Return (risk_level, nearest_zone, dist_km, forecast_dict) or (None,None,None,None).

    When SEL_HORIZON > 0 masses are shifted to their predicted positions first,
    so the returned risk level reflects WHERE sargassum will be at that horizon.
    Falls back to the coarse zone-box forecast when no detections are available.
    """
    if not zones:
        return None, None, None, None

    _lvl, zone, zdist, fc = risk_for_beach(beach, zones, forecast_by_zone_id)

    masses = _cached_fetch_detections(API_BASE_URL)
    if masses:
        horizon = globals().get("SEL_HORIZON") or 0
        if horizon > 0:
            # Shift every mass to its predicted position at this horizon so the
            # per-beach risk reflects the forecast, not just current observations.
            _wu, _wv = _WIND_UV
            shifted: list[dict] = []
            for _m in masses:
                try:
                    _plat, _plon = _predict_position(
                        float(_m["lat"]), float(_m["lon"]), horizon, _wu, _wv
                    )
                    shifted.append({**_m, "lat": _plat, "lon": _plon})
                except Exception:
                    shifted.append(_m)
            working_masses = shifted
        else:
            working_masses = masses

        d_risk, d_mass, d_km, _ = risk_from_detections(beach, working_masses)
        if d_mass is not None:
            return d_risk, zone, d_km, fc
        return "none", zone, None, fc

    # No detections available → fall back to zone-box forecast + distance gate.
    if zone is not None and zdist is not None and zdist > COVERAGE_KM:
        return "out", zone, zdist, None
    return _risk_at_horizon(fc), zone, zdist, fc


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


def _beach_risk_dated(beach: dict, skip_eta: bool = False):
    """Date-aware risk blending physics → ML → seasonal climatology.

    Returns (risk_level, near_zone, dist_km, forecast, mode, beach_eta_h) where:
      mode       — 'physics' | 'ml' | 'seasonal' | 'out' | None
      beach_eta_h — hours until sargassum arrives at this beach (physics only).

    Horizon breakdown:
      0–3 days   → physics drift forecast (Lagrangian + CMEMS)
      4–21 days  → ML extended forecast (GradientBoostingClassifier), falls
                   back to seasonal when no ML data are available
      > 21 days  → seasonal climatology
    """
    import datetime as _d

    visit = globals().get("sel_date")
    days_ahead = (visit - _d.date.today()).days if visit is not None else 0

    # ── 0–3 days: live physics forecast ─────────────────────────────────────
    if visit is None or 0 <= days_ahead <= 3:
        lvl, zone, dist, fc = _beach_risk(beach)
        if lvl is None:
            return None, None, None, None, None, None
        if skip_eta:
            return lvl, zone, dist, fc, ("out" if lvl == "out" else "physics"), None
        masses = _cached_fetch_detections(API_BASE_URL)
        _wu, _wv = _WIND_UV
        eta_h = _beach_eta_quick(beach, masses, wind_u=_wu, wind_v=_wv) if masses else None
        return lvl, zone, dist, fc, ("out" if lvl == "out" else "physics"), eta_h

    # ── 4–21 days: ML extended forecast ─────────────────────────────────────
    if 4 <= days_ahead <= 21:
        # Find the nearest monitoring zone for this beach.
        _, zone, zdist, fc = risk_for_beach(beach, zones, forecast_by_zone_id)
        if zone is not None and zdist is not None and zdist > COVERAGE_KM:
            return "out", zone, zdist, None, "out", None
        ml_data: list[dict] = globals().get("_ml_forecasts") or []
        if zone is not None and ml_data:
            result = _ml_risk_for_zone(zone["id"], days_ahead, ml_data)
            if result:
                risk_lvl, _conf, _method = result
                return risk_lvl, zone, zdist, {"confidence": _conf, "method": _method}, "ml", None
        # Fallback: seasonal climatology when ML data not yet available.
        risk = seasonal_risk(visit.month, beach.get("region"))
        return risk, zone, zdist, None, "seasonal", None

    # ── > 21 days: seasonal climatology ─────────────────────────────────────
    risk = seasonal_risk(visit.month, beach.get("region"))
    return risk, None, None, None, "seasonal", None


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
# Lagrangian drift — region-aware currents + live wind + direction filter
#
# Three accuracy improvements over a single constant vector:
#
#  1. REGIONAL CURRENTS — different coasts of Hispaniola sit in different
#     current regimes (strong NEC on the east, gyre-driven north coast,
#     weaker southwest).  A per-location lookup replaces a global mean.
#
#  2. LIVE WIND DRIFT — Open-Meteo provides free, no-key current 10 m wind.
#     We apply WIND_DRIFT_FACTOR (2 %, same as the pipeline) to get the
#     Stokes/windage component and add it to the base current.  Cached 1 h.
#
#  3. DIRECTION-AWARE ETA — only masses whose effective drift has a positive
#     component toward the beach are counted as approaching.  A mass moving
#     away cannot produce a valid arrival estimate.
# ---------------------------------------------------------------------------
import math as _m_drift

_DRIFT_METERS_PER_DEG = 111_320.0
# Windage fraction (same constant as pipeline/config.py WIND_DRIFT_FACTOR).
_WIND_DRIFT_FACTOR = float(os.environ.get("WIND_DRIFT_FACTOR", "0.02"))


def _regional_current(lat: float, lon: float) -> tuple[float, float]:
    """Return climatological mean surface current (u_east, v_north) in m/s.

    Based on HYCOM/Copernicus reanalysis means for the Caribbean near
    Hispaniola.  Five regimes cover the DR coastline:

    East coast (lon > -69.5)
      North Equatorial Current is strongest here; typical ~0.22 m/s westward.
    Samaná Peninsula (18.9–19.5 °N, -69.7–-68.8 °W)
      Open Atlantic-facing shore sits in the NEC main stream; stronger than the
      generic south/central default (~0.18 m/s vs ~0.13 m/s westward).
      Previously this fell into the South/central bin and was under-estimated.
    North coast (lat > 19.4, lon < -70.0)
      NEC weakens; trade-wind-driven current; slight southward component.
    Southwest (lon < -70.5, lat < 19.0)
      NEC branch is much weaker; coastal shoaling slows flow.
    South / central (default)
      Moderate NEC branch; slight northward component.
    """
    if lon > -69.5:                                              # East coast
        return -0.22, -0.01
    if 18.9 <= lat <= 19.5 and -69.7 <= lon <= -68.8:           # Samaná Peninsula
        return -0.18,  0.01
    if lat > 19.4 and lon < -70.0:                               # North coast
        return -0.12, -0.03
    if lon < -70.5 and lat < 19.0:                               # Southwest
        return -0.08,  0.01
    return -0.13,  0.03                                          # South / central default


@st.cache_data(ttl=3600)
def _fetch_wind_uv() -> tuple[float, float]:
    """Fetch current 10 m wind (u_east, v_north) m/s from Open-Meteo (free, no key).

    Cached for 1 hour.  Returns (0, 0) on any network or parse failure so the
    caller always gets a valid — if zero-wind — drift vector.
    """
    try:
        import requests, certifi
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": 19.0, "longitude": -69.8,
                "current": "wind_speed_10m,wind_direction_10m",
                "wind_speed_unit": "ms",
                "timezone": "UTC",
            },
            timeout=5,
            verify=certifi.where(),
        )
        c = resp.json()["current"]
        spd = float(c["wind_speed_10m"])
        # Meteorological convention: FROM direction.  TO = FROM + 180°.
        go = _m_drift.radians((float(c["wind_direction_10m"]) + 180) % 360)
        return spd * _m_drift.sin(go), spd * _m_drift.cos(go)  # (u_east, v_north)
    except Exception:
        return 0.0, 0.0


def _effective_uv(lat: float, lon: float, wind_u: float, wind_v: float) -> tuple[float, float]:
    """Effective surface drift at (lat, lon): regional current + wind Stokes."""
    u_c, v_c = _regional_current(lat, lon)
    return u_c + _WIND_DRIFT_FACTOR * wind_u, v_c + _WIND_DRIFT_FACTOR * wind_v


def _predict_position(lat: float, lon: float, hours: int,
                      wind_u: float = 0.0, wind_v: float = 0.0,
                      _step_h: int = 24) -> tuple[float, float]:
    """Estimate mass centroid after `hours` of Lagrangian advection.

    Uses iterative `_step_h`-hour Euler steps so the regional current is
    re-evaluated at each checkpoint, preventing the large positional errors
    that single-step integration produces for long horizons (> 24 h).

    For the hourly caller in `_beach_eta_quick` (hours=1) the fast single-step
    path is taken.  For ML extended mode (hours up to 504 h) iterating in
    24-hour chunks re-computes the velocity at each waypoint, keeping the
    trajectory inside the Caribbean rather than drifting off into the Atlantic.
    """
    if hours <= _step_h:
        # Fast path — single-step Euler (used for 1-h calls in _beach_eta_quick)
        u_eff, v_eff = _effective_uv(lat, lon, wind_u, wind_v)
        dt_sec = hours * 3600.0
        new_lat = lat + (v_eff * dt_sec) / _DRIFT_METERS_PER_DEG
        new_lon = lon + (u_eff * dt_sec) / (
            _m_drift.cos(_m_drift.radians(lat)) * _DRIFT_METERS_PER_DEG
        )
        return new_lat, new_lon
    # Iterative path — re-evaluate velocity at every _step_h-hour waypoint.
    cur_lat, cur_lon = lat, lon
    remaining = hours
    while remaining > 0:
        h = min(_step_h, remaining)
        u_eff, v_eff = _effective_uv(cur_lat, cur_lon, wind_u, wind_v)
        dt_sec = h * 3600.0
        cur_lat += (v_eff * dt_sec) / _DRIFT_METERS_PER_DEG
        cur_lon += (u_eff * dt_sec) / (
            _m_drift.cos(_m_drift.radians(cur_lat)) * _DRIFT_METERS_PER_DEG
        )
        remaining -= h
    return cur_lat, cur_lon


def _beach_eta_quick(beach: dict, masses: list[dict], max_hours: int = 72,
                     wind_u: float = 0.0, wind_v: float = 0.0) -> int | None:
    """Hours until the nearest approaching mass drifts within ARRIVAL_KM of this beach.

    Improvements over the previous hour-by-hour loop:
    • Uses region-aware + wind-corrected drift so direction is realistic.
    • Filters out masses whose effective drift has NO component toward the
      beach (approach_speed ≤ 0) — they cannot arrive in finite time under
      the current flow and should not generate a spurious ETA.
    • Uses an analytic first-estimate (distance / approach_speed) to rank
      candidates, then verifies only the best one with an exact hour-by-hour
      simulation for precision.
    """
    from dashboard.risk_overlay import haversine_km as _hkm

    ARRIVAL_KM = 12.0          # mass is "at the beach" when this close
    MAX_SPEED_KMH = 1.2        # conservative upper bound on drift speed (km/h)
    search_km = ARRIVAL_KM + MAX_SPEED_KMH * max_hours  # pre-filter radius

    blat = float(beach["latitude"])
    blon = float(beach["longitude"])

    best_eta: int | None = None

    for m in masses:
        try:
            mlat, mlon = float(m["lat"]), float(m["lon"])
        except Exception:
            continue

        dist = _hkm(blat, blon, mlat, mlon)
        if dist > search_km:
            continue
        if dist <= ARRIVAL_KM:
            return 0

        # ── Direction filter ────────────────────────────────────────────────
        # Compute the component of drift velocity pointing toward the beach.
        # If it is ≤ 0 the mass is stationary or moving away — skip it.
        u_eff, v_eff = _effective_uv(mlat, mlon, wind_u, wind_v)
        # Unit vector from mass to beach (in km-space, lat/lon corrected).
        dlat_km = (blat - mlat) * 111.32
        dlon_km = (blon - mlon) * 111.32 * _m_drift.cos(_m_drift.radians(mlat))
        approach_ms = (v_eff * dlat_km + u_eff * dlon_km) / dist  # m/s toward beach
        if approach_ms <= 0.005:   # not meaningfully approaching (threshold: ~18 m/h)
            continue

        # ── Analytic first-estimate ──────────────────────────────────────────
        approach_kmh = approach_ms * 3.6
        eta_est = int(_m_drift.ceil((dist - ARRIVAL_KM) / approach_kmh))
        if eta_est > max_hours:
            continue

        # ── Exact hour-by-hour refinement ───────────────────────────────────
        # Verify with the simulation because the analytic estimate assumes a
        # straight-line path; curved trajectories can shift arrival by a few h.
        c = [mlat, mlon]
        for h in range(max_hours + 1):
            if _hkm(blat, blon, c[0], c[1]) <= ARRIVAL_KM:
                eta_est = h
                break
            if h < max_hours:
                c[0], c[1] = _predict_position(c[0], c[1], 1, wind_u, wind_v)
        else:
            continue  # analytic said it'd arrive but simulation disagrees → skip

        if best_eta is None or eta_est < best_eta:
            best_eta = eta_est

    return best_eta


def _recommend_beaches(beach: dict, all_beaches: list[dict], n: int = 3) -> list[dict]:
    """Return up to n beaches in the same region ranked by shared activities.

    Excludes the current beach itself and limits to the same coastal region so
    the suggestions are geographically relevant.
    """
    acts = set(beach.get("activities") or [])
    same_region = [
        b for b in all_beaches
        if b["name"] != beach["name"] and b["region"] == beach["region"]
    ]
    return sorted(
        same_region,
        key=lambda b: len(acts & set(b.get("activities") or [])),
        reverse=True,
    )[:n]


# Populate wind once, after _fetch_wind_uv is defined.
_WIND_UV = _fetch_wind_uv()

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
    beach_search = st.text_input(
        L["search_beach"], "", key="beach_search",
        placeholder="Ej: Rincón, Sosúa, Bavaro…",
    )
    sel_activities = st.multiselect(L["activity"], all_activities(), default=[])
    protected_only = st.checkbox(L["protected_only"], value=False)
    free_only = st.checkbox(L["free_only"], value=False)

    # Filter by current sargassum risk level. Options are localized labels but
    # map back to the canonical risk keys. Empty = show all risk levels.
    sel_risks = st.multiselect(
        L["risk_filter"],
        options=["high", "medium", "low", "none"],
        default=[],
        format_func=lambda k: f"{RISK_EMOJI.get(k, '')} {RISK_LABEL.get(k, k)}".strip(),
        key="risk_filter",
    )

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
            f"<div style='margin-bottom:6px'><strong style='color:#ce93d8'>🤖 {L['prediction_ml']}</strong></div>"
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
        elif 4 <= _days_ahead <= 21:
            _has_ml = bool(_ml_forecasts)
            _ml_src = "ML" if _has_ml else ("Climatología" if lang == "es" else "Climatology")
            st.caption(f"🤖 {_ml_src} — {_days_ahead}d ahead")
        else:
            st.caption("📊 " + L["method_seasonal"])


# Per-render cache: beach name → _beach_risk_dated result WITHOUT ETA.
# Avoids calling _beach_risk_dated twice per beach (once in _matches for the
# risk filter, once again in the marker-building loop) and avoids running the
# O(masses × 72h) _beach_eta_quick loop for every beach on every render.
# ETA is only computed for the single selected beach in the detail panel.
_BEACH_RISK_CACHE: dict[str, tuple] = {}


def _beach_risk_cached(beach: dict, compute_eta: bool = False) -> tuple:
    """Return _beach_risk_dated, computing ETA only when compute_eta=True.

    Use compute_eta=True only for the one beach shown in the detail panel.
    All other callers (filter check, marker loop) should leave it False so
    the expensive _beach_eta_quick loop runs at most once per render.
    """
    name = beach["name"]
    if name not in _BEACH_RISK_CACHE:
        _BEACH_RISK_CACHE[name] = _beach_risk_dated(beach, skip_eta=True)
    if not compute_eta:
        return _BEACH_RISK_CACHE[name]
    # Full call with ETA — only invoked for the panel beach.
    return _beach_risk_dated(beach, skip_eta=False)


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
    if beach_search and beach_search.strip().lower() not in beach["name"].lower():
        return False
    # Risk filter — compute this beach's current risk level and keep it only if
    # it matches one of the selected levels. A beach with no risk data (None)
    # is treated as 'none' so the filter behaves intuitively.
    if sel_risks:
        _beach_lvl = _beach_risk_cached(beach)[0] or "none"
        # 'out' (outside monitored area) counts as 'none' for filtering.
        if _beach_lvl == "out":
            _beach_lvl = "none"
        if _beach_lvl not in sel_risks:
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
# Global date-mode — drives zone colours, mass drift style, and beach badges.
# ---------------------------------------------------------------------------
import datetime as _dt_global

_today_g = _dt_global.date.today()
_days_ahead_g = (sel_date - _today_g).days if sel_date is not None else 0
# mode: 'physics' | 'ml' | 'seasonal'
if sel_date is None or _days_ahead_g <= 3:
    _date_mode = "physics"
elif _days_ahead_g <= 21:
    _date_mode = "ml"
else:
    _date_mode = "seasonal"

_ml_drift_mode = (_date_mode == "ml")  # drives mass styling + legend

# ---------------------------------------------------------------------------
# Build Folium map
# ---------------------------------------------------------------------------
m = folium.Map(location=[19.0, -69.8], zoom_start=7, tiles="OpenStreetMap")
# Make the inner Leaflet map fill the full viewport height. st_folium renders
# the map inside a FIXED-height (900px) folium Figure, so on tall/full screens a
# blank strip appears below the map even after our CSS stretches the OUTER
# iframe to 100vh. Forcing the map div itself to 100vh (which equals the window
# height because the outer iframe is already 100vh) removes that strip.
# IMPORTANT: only size the map — never reposition .leaflet-container
# (position:absolute) because Leaflet caches container geometry at init and
# blanks out if it is moved after load. Sizing before init is safe.
m.get_root().header.add_child(folium.Element(
    "<style>"
    "html, body { height: 100%; margin: 0; padding: 0; }"
    ".folium-map { height: 100vh !important; width: 100% !important; }"
    "</style>"
))
cluster = MarkerCluster(
    options={"maxClusterRadius": 50, "disableClusteringAtZoom": 11}
).add_to(m)

for b in filtered:
    # Beach markers are WHITE pins with a COLORED RING (region color).
    # This visually separates beaches from risk zones (filled colored rectangles).
    region_color = REGION_COLORS.get(b["region"], "#1f77b4")
    risk_level, near_zone, dist_km_b, _, _mode_b, _eta_b = _beach_risk_cached(b)
    turtle_icon = " 🐢" if b["protected_area"] else ""
    desc_short = (b["description"] or "")[:170].rstrip()
    if len(b["description"] or "") > 170:
        desc_short += "…"

    # Per-beach ETA line for the popup (drift estimate)
    _eta_popup = ""
    if _eta_b is not None and _mode_b == "physics":
        if _eta_b == 0:
            _eta_popup = (
                "<div style='color:#ffcdd2;font-size:10.5px;margin-top:3px'>"
                "⚠️ Sargazo ya cerca / Already nearby</div>"
            )
        else:
            _eta_popup = (
                f"<div style='color:#b2ebf2;font-size:10.5px;margin-top:3px'>"
                f"⏱️ Llegada estimada / ETA: ~{_eta_b}h</div>"
            )

    # Risk badge — always present
    if _mode_b == "ml" and risk_level is not None:
        rc = RISK_COLORS.get(risk_level, "#6c757d")
        label_txt = RISK_LABEL.get(risk_level, risk_level.upper())
        _ml_conf_pct = 40
        if near_zone and _ml_forecasts:
            _zid_b = near_zone.get("id")
            _mf_hit = next((f for f in _ml_forecasts if f.get("zone_id") == _zid_b), None)
            if _mf_hit:
                _ml_conf_pct = int(_mf_hit.get("confidence", 0.4) * 100)
        risk_badge = (
            f"<div style='background:{rc};color:#fff;display:inline-block;"
            f"padding:3px 12px;border-radius:20px;font-size:11px;font-weight:700;margin:5px 0'>"
            f"🌊 Sargazo: {label_txt}"
            f"</div>"
            f"<div style='color:#ce93d8;font-size:10.5px;margin-top:4px'>"
            f"🤖 {L['ml_badge']} · {_ml_conf_pct}%</div>"
        )
    elif _mode_b == "seasonal" and risk_level is not None:
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
        _dist_txt = (
            f" · ~{dist_km_b:.0f} km"
            if dist_km_b is not None else ""
        )
        risk_badge = (
            f"<div style='background:{rc};color:#fff;display:inline-block;"
            f"padding:3px 12px;border-radius:20px;font-size:11px;font-weight:700;margin:5px 0'>"
            f"🌊 Sargazo: {label_txt}{_dist_txt}"
            f"</div>{_eta_popup}"
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
_ZONE_BOX_DEG = _ZONE_HALF_DEG  # half-width, kept in sync with pipeline/config.py ZONE_BOX_HALF_DEG

def _zone_region(clat: float, clon: float) -> str:
    """Map a zone's centre coordinates to a DR coastal region for seasonal climatology."""
    if clon > -69.0:
        return "East (Punta Cana / La Romana)"
    if clat > 19.5:
        return "North (Puerto Plata / Cabarete)"
    if -69.5 < clon < -69.0 and clat > 18.9:
        return "Samaná Peninsula"
    if clon < -70.5:
        return "Southwest (Barahona / Pedernales)"
    return "South (Santo Domingo / South Coast)"


if show_zones and zones:
    for _z in zones:
        _zid = _z["id"]
        _fc_z = forecast_by_zone_id.get(_zid)
        _clat = _z["center_lat"]
        _clon = _z["center_lon"]

        if _date_mode == "ml":
            # ML window: pick the forecast for this zone at the closest lead day
            _ml_hit = _ml_risk_for_zone(_zid, _days_ahead_g, _ml_forecasts) if _ml_forecasts else None
            if _ml_hit:
                _z_risk, _z_conf, _ = _ml_hit
            else:
                _z_risk = seasonal_risk(sel_date.month, _zone_region(_clat, _clon))
                _z_conf = 0.4
        elif _date_mode == "seasonal":
            _z_risk = seasonal_risk(sel_date.month, _zone_region(_clat, _clon))
            _z_conf = 0.0
        else:
            _z_risk = _risk_at_horizon(_fc_z) if _fc_z else "none"
            _z_conf = 1.0

        _z_color = RISK_COLORS.get(_z_risk, "#6c757d")
        _z_label = RISK_LABEL.get(_z_risk, _z_risk.upper())

        # Build popup extra lines
        if _date_mode == "ml":
            _z_conf_pct = int(_z_conf * 100)
            _ml_lead = min([7, 14, 21], key=lambda d: abs(d - _days_ahead_g))
            _ml_valid = (sel_date.isoformat() if sel_date else "")
            _z_extra = (
                f"<div style='background:rgba(123,31,162,.2);border-radius:12px;"
                f"padding:3px 10px;font-size:10px;color:#ce93d8;margin-top:4px'>"
                f"🤖 {L['ml_badge']} · +{_ml_lead}d</div>"
                f"<div style='color:#ce93d8;font-size:10px;margin-top:3px'>"
                f"{L['ml_confidence']}: {_z_conf_pct}%</div>"
                + (f"<div style='color:#80cbc4;font-size:10px;margin-top:3px'>📅 {_ml_valid}</div>"
                   if _ml_valid else "")
            )
        elif _date_mode == "seasonal":
            _z_extra = (
                f"<div style='background:rgba(255,193,7,.2);border-radius:12px;"
                f"padding:3px 10px;font-size:10px;color:#ffe082;margin-top:4px'>"
                f"📊 {L['seasonal_badge']}</div>"
                f"<div style='color:#80cbc4;font-size:10px;margin-top:4px'>"
                f"{L['method_seasonal']}</div>"
            )
        else:
            _z_extra = (
                (
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
            )

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
                + _z_extra
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
# Initialised here so _show_drift_legend (below) is always safe, even when
# show_masses=True but the API returns no detections.
_drift_h: int = 0
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

        # Determine how many hours ahead to preview drift.
        #   0–72 h  → physics slider (SEL_HORIZON)
        #   4–21 d  → ML window: extrapolate full horizon (speculative — faded style)
        #   > 21 d  → seasonal; no mass position is meaningful, cap at 72 h for direction
        import datetime as _dt_drift
        _drift_h: int = SEL_HORIZON if SEL_HORIZON is not None else 0
        if sel_date is not None:
            _ddays = (sel_date - _dt_drift.date.today()).days
            if 0 < _ddays <= 3:
                _drift_h = min(_ddays * 24, 72)
            elif 4 <= _ddays <= 21:
                _drift_h = _ddays * 24   # up to 504 h — speculative
            elif _ddays > 21:
                _drift_h = 72  # only show direction, not position
        _show_drift = _drift_h > 0

        _mass_group = folium.FeatureGroup(name="Sargazo", show=True)
        for _d in _masses:
            try:
                _a = float(_d.get("area_km2", 0.0))
            except (TypeError, ValueError):
                _a = 0.0
            _lat0, _lon0 = float(_d["lat"]), float(_d["lon"])
            # Radius scales with area (sqrt so big masses don't dominate); clamp 3–18 px.
            _r = max(3.0, min(18.0, 3.0 + (_a ** 0.5) * 4.0))

            if _show_drift:
                if _ml_drift_mode:
                    # ML / speculative mode — build a multi-segment trail with
                    # checkpoints every 7 days so the path is visible but not
                    # overcrowded. Trail color shifts from brown → purple to
                    # signal increasing uncertainty.
                    _cp_days = [d for d in [3, 7, 14, 21]
                                if d * 24 <= _drift_h]
                    _checkpoints = [d * 24 for d in _cp_days] or [_drift_h]
                    _trail = [[_lat0, _lon0]]
                    for _ch in _checkpoints:
                        _pl, _pn = _predict_position(_lat0, _lon0, _ch)
                        _trail.append([_pl, _pn])
                    # Outer faded trail (full path)
                    folium.PolyLine(
                        _trail,
                        color="#7b1fa2",
                        weight=1.5,
                        opacity=0.35,
                        dash_array="6 6",
                        tooltip=f"🟣 Ruta especulativa ML · {_a:.2f} km²",
                    ).add_to(_mass_group)
                    # Short physics segment (first 72 h) in the normal brown
                    _trail_72 = [[_lat0, _lon0]]
                    _pl72, _pn72 = _predict_position(_lat0, _lon0, 72)
                    _trail_72.append([_pl72, _pn72])
                    folium.PolyLine(
                        _trail_72,
                        color="#8d6e63",
                        weight=1.5,
                        opacity=0.55,
                        dash_array="5 4",
                    ).add_to(_mass_group)
                    # Uncertainty halo at predicted position — larger radius
                    _plat, _plon = _trail[-1]
                    folium.CircleMarker(
                        location=[_plat, _plon],
                        radius=max(6.0, _r + 4),
                        color="#ce93d8",
                        fill=True,
                        fill_color="#e1bee7",
                        fill_opacity=0.20,
                        weight=1.5,
                        dash_array="4 3",
                        tooltip=f"🟣 Sargazo especulativo (+{_drift_h}h ML) · {_a:.2f} km²",
                    ).add_to(_mass_group)
                    # Smaller solid dot at predicted centre
                    folium.CircleMarker(
                        location=[_plat, _plon],
                        radius=max(3.0, _r - 2),
                        color="#7b1fa2",
                        fill=True,
                        fill_color="#ce93d8",
                        fill_opacity=0.45,
                        weight=1.0,
                        tooltip=f"🟣 Sargazo especulativo (+{_drift_h}h ML) · {_a:.2f} km²",
                    ).add_to(_mass_group)
                else:
                    # Physics mode — original 4-point trail
                    _checkpoints = sorted({24, 48, _drift_h} & {h for h in [24, 48, 72] if h <= _drift_h})
                    _trail = [[_lat0, _lon0]]
                    for _ch in _checkpoints:
                        _pl, _pn = _predict_position(_lat0, _lon0, _ch)
                        _trail.append([_pl, _pn])

                    # Drift trail — thin dashed line
                    folium.PolyLine(
                        _trail,
                        color="#8d6e63",
                        weight=1.5,
                        opacity=0.65,
                        dash_array="5 4",
                        tooltip=f"🟤 Ruta estimada · {_a:.2f} km²",
                    ).add_to(_mass_group)

                # Physics ghost circle (only for non-ML mode)
                if not _ml_drift_mode:
                    _plat, _plon = _trail[-1]
                    folium.CircleMarker(
                        location=[_plat, _plon],
                        radius=max(2.5, _r - 2),
                        color="#ff8f00",
                        fill=True,
                        fill_color="#ffcc80",
                        fill_opacity=0.55,
                        weight=1.5,
                        dash_array="4 3",
                        tooltip=f"🟠 Sargazo (+{_drift_h}h estimado) · {_a:.2f} km²",
                    ).add_to(_mass_group)

            # Current detected position — fainter in ML mode to signal mass has moved
            folium.CircleMarker(
                location=[_lat0, _lon0],
                radius=_r,
                color="#5d4037",
                fill=True,
                fill_color="#795548",
                fill_opacity=0.20 if _ml_drift_mode else 0.55,
                weight=1,
                tooltip=f"🟤 Sargazo · {_a:.2f} km²",
            ).add_to(_mass_group)

        _mass_group.add_to(m)
    # If no masses came back we silently skip — sidebar caption explains why below.

# Floating legend — show risk zones + drift key when masses are visible.
_show_drift_legend = show_masses and _drift_h > 0
if zones:
    risk_legend_rows = "".join(
        f"<div style='display:flex;align-items:center;gap:7px;margin:2px 0'>"
        f"<div style='width:16px;height:10px;background:{c};flex-shrink:0;border-radius:2px'></div>"
        f"<span style='font-size:11px;color:#37474f'>{RISK_LABEL.get(lv, lv)}</span></div>"
        for lv, c in RISK_COLORS.items()
    )
    legend_html = (
        f"<div style='font-weight:800;font-size:11.5px;color:#005f73;margin-bottom:3px'>"
        f"{L['zone_legend']}</div>{risk_legend_rows}"
    )
else:
    legend_html = ""

if _show_drift_legend:
    if _ml_drift_mode:
        _drift_days = _drift_h // 24
        _drift_label = f"Pos. especulativa ML (+{_drift_days}d)" if lang == "es" else f"ML speculative pos. (+{_drift_days}d)"
        legend_html += (
            f"<div style='margin-top:6px;padding-top:5px;border-top:1px solid #cfd8dc'>"
            f"<div style='font-weight:800;font-size:11px;color:#5e35b1;margin-bottom:3px'>"
            f"{'Sargazo — vista extendida ML' if lang == 'es' else 'Sargassum — ML extended view'}</div>"
            f"<div style='display:flex;align-items:center;gap:7px;margin:2px 0'>"
            f"<div style='width:16px;height:10px;background:#795548;border-radius:50%;opacity:.35'></div>"
            f"<span style='font-size:11px;color:#37474f'>{'Posición actual (detectada)' if lang == 'es' else 'Current detected position'}</span></div>"
            f"<div style='display:flex;align-items:center;gap:7px;margin:2px 0'>"
            f"<div style='width:16px;height:10px;background:#e1bee7;border:1.5px dashed #7b1fa2;border-radius:50%'></div>"
            f"<span style='font-size:11px;color:#37474f'>{_drift_label}</span></div>"
            f"<div style='font-size:9.5px;color:#78909c;margin-top:3px'>{'⚠️ Incertidumbre alta — solo indica dirección' if lang == 'es' else '⚠️ High uncertainty — indicates direction only'}</div>"
            f"</div>"
        )
    else:
        _drift_label = "Pos. estimada" if lang == "es" else "Est. position"
        legend_html += (
            f"<div style='margin-top:6px;padding-top:5px;border-top:1px solid #cfd8dc'>"
            f"<div style='font-weight:800;font-size:11px;color:#005f73;margin-bottom:3px'>"
            f"{'Sargazo — deriva' if lang == 'es' else 'Sargassum — drift'}</div>"
            f"<div style='display:flex;align-items:center;gap:7px;margin:2px 0'>"
            f"<div style='width:16px;height:10px;background:#795548;border-radius:50%'></div>"
            f"<span style='font-size:11px;color:#37474f'>{'Posición actual' if lang == 'es' else 'Current position'}</span></div>"
            f"<div style='display:flex;align-items:center;gap:7px;margin:2px 0'>"
            f"<div style='width:16px;height:10px;background:#ffcc80;border:1.5px dashed #ff8f00;border-radius:50%'></div>"
            f"<span style='font-size:11px;color:#37474f'>{_drift_label} (+{_drift_h}h)</span></div>"
            f"<div style='font-size:9.5px;color:#78909c;margin-top:3px'>{'Corriente media del Caribe' if lang == 'es' else 'Caribbean mean current'}</div>"
            f"</div>"
        )

m.get_root().html.add_child(folium.Element(
    f"<div style='position:absolute;top:10px;left:60px;z-index:1000;"
    f"background:rgba(0,96,100,.88);color:#fff;border-radius:10px;"
    f"padding:8px 14px;font-size:12px;font-weight:700;backdrop-filter:blur(4px)'>"
    f"{L['tip']}</div>"
))
if _ml_drift_mode:
    m.get_root().html.add_child(folium.Element(
        f"<div style='position:absolute;bottom:36px;left:60px;z-index:1000;"
        f"background:rgba(74,20,140,.85);color:#e1bee7;border-radius:10px;"
        f"padding:7px 13px;font-size:11px;font-weight:700;backdrop-filter:blur(4px);"
        f"border:1px solid #7b1fa2'>"
        f"🤖 {L['ml_note_map']} (+{_days_ahead_g}d)</div>"
    ))

# Render map — stable key preserves pan/zoom between beach selections
map_result = st_folium(
    m,
    width="100%",
    height=900,
    returned_objects=["last_object_clicked_tooltip", "last_object_clicked", "last_clicked"],
    key="beach_map",
)


# Sync map click → session state (robust: tooltip -> object -> coords fallback)
def _select_beach_from_map_result(res: dict | None) -> None:
    if not res:
        return

    # 1) Prefer tooltip text when available (our standard: '🏖️ Name — Province')
    tip = res.get("last_object_clicked_tooltip")
    if tip:
        try:
            clicked = tip.replace("🏖️ ", "").split(" — ")[0].strip()
        except Exception:
            clicked = tip.strip()
        if clicked in filtered_names and clicked != st.session_state.get("selected_beach"):
            st.session_state["selected_beach"] = clicked
            st.rerun()
            return

    # 2) Try last_object_clicked (may contain properties/name)
    obj = res.get("last_object_clicked")
    if obj and isinstance(obj, dict):
        # Attempt common property names
        for key in ("tooltip", "name", "title", "popup"):
            val = obj.get(key)
            if isinstance(val, str):
                cand = val.replace("🏖️ ", "").split(" — ")[0].strip()
                if cand in filtered_names and cand != st.session_state.get("selected_beach"):
                    st.session_state["selected_beach"] = cand
                    st.rerun()
                    return

    # 3) Fallback: use last_clicked coordinates and pick the nearest filtered beach
    last = res.get("last_clicked")
    if last and isinstance(last, dict):
        try:
            lat = float(last.get("lat") or last.get("latitude"))
            lon = float(last.get("lng") or last.get("lon") or last.get("longitude"))
        except Exception:
            return
        # Find nearest beach among the currently filtered set (within ~0.5 km)
        best = None
        best_km = float("inf")
        for b in (b for b in filtered):
            d = haversine_km(lat, lon, float(b["latitude"]), float(b["longitude"]))
            if d < best_km:
                best_km = d
                best = b
        if best and best_km <= 0.6:  # ~600 m tolerance to avoid misclicks
            if best["name"] != st.session_state.get("selected_beach"):
                st.session_state["selected_beach"] = best["name"]
                st.rerun()


_select_beach_from_map_result(map_result)

# Sync selected beach back to the URL so the current view is shareable via link.
if st.session_state.get("selected_beach"):
    st.query_params["beach"] = st.session_state["selected_beach"]

# ---------------------------------------------------------------------------
# Legend — rendered in the Streamlit DOM (position:fixed) so it is NEVER
# clipped by the Folium iframe viewport (bottom-left, always visible).
# ---------------------------------------------------------------------------
if legend_html:
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
    _risk_level, _near_zone, _dist_km, _fc, _mode, _beach_eta = _beach_risk_cached(_pb, compute_eta=True)
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
    if _mode == "ml" and _risk_level and _near_zone:
        _rc = RISK_COLORS.get(_risk_level, "#607d8b")
        _rlbl = RISK_LABEL.get(_risk_level, _risk_level.upper())
        _emoji, _advice_es, _advice_en = _ADVISORY.get(_risk_level, ("⚪", "", ""))
        _advice = _advice_en if lang == "en" else _advice_es
        _ml_conf_panel = int((_fc or {}).get("confidence", 0.4) * 100)
        _ml_lead_panel = min([7, 14, 21], key=lambda d: abs(d - _days_ahead_g))
        _zone_name = _near_zone["name"] if _near_zone else ""
        _risk_section = (
            f"<div style='background:rgba(0,0,0,.25);border-radius:12px;"
            f"padding:10px 12px;margin:9px 0;border-left:4px solid {_rc}'>"
            f"<div style='display:flex;align-items:center;gap:8px;margin-bottom:6px'>"
            f"<span style='background:{_rc};color:#fff;border-radius:16px;"
            f"padding:2px 10px;font-size:11px;font-weight:800'>{_emoji} {_rlbl}</span>"
            f"<span style='background:rgba(123,31,162,.25);color:#ce93d8;border-radius:16px;"
            f"padding:2px 10px;font-size:10px;font-weight:700'>🤖 {L['ml_badge']}</span>"
            f"</div>"
            f"<div style='color:#cfd8dc;font-size:11.5px;line-height:1.45;margin-bottom:6px'>{_advice}</div>"
            f"<div style='background:rgba(0,0,0,.2);border-radius:8px;padding:6px 10px;margin:5px 0'>"
            f"<div style='display:flex;justify-content:space-between;align-items:center'>"
            f"<span style='color:#80cbc4;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px'>"
            f"📅 {'Pronóstico' if lang == 'es' else 'Forecast'} +{_ml_lead_panel}d</span>"
            f"<span style='color:#e0f7fa;font-size:13px;font-weight:800'>{_rlbl}</span>"
            f"</div>"
            f"<div style='color:#546e7a;font-size:9.5px;margin-top:2px'>"
            f"{'Zona' if lang == 'es' else 'Zone'}: {_zone_name} · {L['ml_confidence']}: {_ml_conf_panel}%</div>"
            f"</div>"
            f"<div style='color:#ce93d8;font-size:10.5px;line-height:1.4;margin-bottom:4px'>⚠️ {L['ml_advisory']}</div>"
            f"<div style='color:#80cbc4;font-size:10px;border-top:1px solid rgba(255,255,255,.1);"
            f"padding-top:5px;margin-top:5px'>{L['method_ml'].format(conf=_ml_conf_panel)}</div>"
            f"</div>"
        )
    elif _mode == "seasonal" and _risk_level:
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

        # ETA lines — per-beach drift estimate (primary) + zone run timestamp
        _eta_line = ""

        # Per-beach arrival estimate from the drift simulation.
        if _beach_eta is not None:
            if _beach_eta == 0:
                _eta_line += (
                    f"<div style='background:rgba(220,53,69,.2);border-radius:8px;"
                    f"padding:6px 10px;margin:5px 0;display:flex;align-items:center;gap:8px'>"
                    f"<span style='font-size:15px'>⚠️</span>"
                    f"<span style='color:#ffcdd2;font-size:12px;font-weight:700'>"
                    f"{'Sargazo ya cerca de esta playa' if lang == 'es' else 'Sargassum already near this beach'}"
                    f"</span></div>"
                )
            else:
                _eta_line += (
                    f"<div style='background:rgba(0,0,0,.2);border-radius:8px;"
                    f"padding:6px 10px;margin:5px 0'>"
                    f"<div style='display:flex;justify-content:space-between;align-items:center'>"
                    f"<span style='color:#80cbc4;font-size:10px;font-weight:700;"
                    f"text-transform:uppercase;letter-spacing:.5px'>"
                    f"⏱️ {'Llegada a esta playa' if lang == 'es' else 'Arrival at this beach'}</span>"
                    f"<span style='color:#e0f7fa;font-size:13px;font-weight:800'>~{_beach_eta}h</span>"
                    f"</div>"
                    f"<div style='color:#546e7a;font-size:9.5px;margin-top:2px'>"
                    f"{'Estimado · corriente media del Caribe' if lang == 'es' else 'Estimated · Caribbean mean current'}"
                    f"</div></div>"
                )

        # Zone-level run timestamp (shows data freshness).
        if _fc and _fc.get("run_at"):
            _run_at = _fc.get("run_at", "")
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

        # Coastal zone name for context + nearest DETECTED MASS distance
        # (beach-specific). The km is to the actual sargassum, not the zone.
        _zone_name = _near_zone["name"] if _near_zone else ""
        _mass_dist_txt = (
            f" &nbsp;·&nbsp; sargazo ~{_dist_km:.0f} km"
            if _dist_km is not None else ""
        )
        _risk_section = (
            f"<div style='background:rgba(0,0,0,.25);border-radius:12px;"
            f"padding:10px 12px;margin:9px 0;border-left:4px solid {_rc}'>"
            # Header row: badge + zone
            f"<div style='display:flex;align-items:center;gap:8px;margin-bottom:6px'>"
            f"<span style='background:{_rc};color:#fff;border-radius:16px;"
            f"padding:2px 10px;font-size:11px;font-weight:800'>{_emoji} {_rlbl}</span>"
            f"<span style='color:#80cbc4;font-size:11px'>{_zone_name}"
            f"{_mass_dist_txt}</span></div>"
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

    # Recommendations — similar beaches in the same region (same activities).
    _recs = _recommend_beaches(_pb, BEACHES)
    if _recs:
        _rec_items = ""
        for _rec_b in _recs:
            _rec_name = _rec_b["name"]
            _rec_prov = _rec_b["province"]
            _turtle_r = " 🐢" if _rec_b.get("protected_area") else ""
            _rec_items += (
                f"<a href='?beach={_rec_name}' style='display:block;"
                f"background:rgba(0,180,130,.12);border:1px solid rgba(0,255,180,.18);"
                f"border-radius:10px;padding:7px 10px;margin:4px 0;"
                f"color:#b2ebf2;font-size:11px;font-weight:700;text-decoration:none'>"
                f"🏖️ {_rec_name}{_turtle_r}"
                f"<span style='color:#80cbc4;font-weight:400'> · {_rec_prov}</span></a>"
            )
        _recs_html = (
            "<hr style='border:none;border-top:1px solid rgba(255,255,255,.13);margin:9px 0'>"
            f"<div style='color:#80cbc4;font-size:10px;font-weight:700;"
            f"text-transform:uppercase;letter-spacing:.5px;margin-bottom:5px'>"
            f"✨ {L['recommendations']}</div>"
            + _rec_items
        )
    else:
        _recs_html = ""

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
        + _recs_html
        + "</div>"
    )
    st.markdown(_bubble, unsafe_allow_html=True)


