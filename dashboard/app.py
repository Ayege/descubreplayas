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

"""Streamlit dashboard: interactive risk map of the DR coast with zone forecasts.

Data is fetched from the FastAPI service at API_BASE_URL (env var or .env).
Deploy free: push repo to GitHub, then connect at share.streamlit.io.
"""
from __future__ import annotations

import os

import certifi
import folium
import requests
import streamlit as st
from dotenv import load_dotenv
from streamlit_folium import st_folium

load_dotenv()

API_BASE_URL = os.environ.get("API_BASE_URL", "https://api.descubreplayas.com.do")

# Risk level -> colour for the zone circles on the map.
_RISK_COLOR = {
    "none": "#6c757d",    # grey
    "low": "#28a745",     # green
    "medium": "#fd7e14",  # orange
    "high": "#dc3545",    # red
}
_RISK_EMOJI = {"none": "⚪", "low": "🟢", "medium": "🟠", "high": "🔴"}


# ---------------------------------------------------------------------------
# Data fetching (cached 5 min)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def fetch_forecasts() -> list[dict]:
    try:
        resp = requests.get(f"{API_BASE_URL}/forecast", timeout=10, verify=certifi.where())
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        st.warning(f"Could not fetch forecasts: {exc}")
        return []


@st.cache_data(ttl=300)
def fetch_zones() -> list[dict]:
    try:
        resp = requests.get(f"{API_BASE_URL}/zones", timeout=10, verify=certifi.where())
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        st.warning(f"Could not fetch zones: {exc}")
        return []


# ---------------------------------------------------------------------------
# App layout
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Sargazo RD — Mapa de Riesgo",
    page_icon="🌊",
    layout="wide",
)

st.title("🌊 Sargazo RD — Sistema de Alerta Temprana")
st.caption(f"Datos: {API_BASE_URL}")

forecasts = fetch_forecasts()
zones = fetch_zones()

# Build a lookup: zone_id -> forecast row
fc_by_zone: dict[int, dict] = {f["zone_id"]: f for f in forecasts}

# ---------------------------------------------------------------------------
# Sidebar — zone list
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Pronóstico por zona")
    if not forecasts:
        st.info("Sin datos. Verifica que la API esté corriendo.")
    for fc in sorted(forecasts, key=lambda x: x.get("zone_id", 0)):
        risk = fc.get("risk_level", "none")
        emoji = _RISK_EMOJI.get(risk, "⚪")
        eta = fc.get("eta_hours")
        eta_str = f"{eta}h" if eta else "N/A"
        st.markdown(
            f"**{emoji} {fc.get('name', 'Zona')}**  \n"
            f"Riesgo: `{risk.upper()}` | ETA: `{eta_str}`"
        )
    st.divider()
    st.caption("Actualiza cada 5 min. Fuente: Sentinel-2 + CMEMS + Open-Meteo.")

# ---------------------------------------------------------------------------
# Folium map — DR coast centred
# ---------------------------------------------------------------------------

m = folium.Map(location=[19.0, -69.9], zoom_start=7, tiles="OpenStreetMap")

for zone in zones:
    zid = zone["id"]
    fc = fc_by_zone.get(zid, {})
    risk = fc.get("risk_level", "none")
    color = _RISK_COLOR.get(risk, _RISK_COLOR["none"])
    eta = fc.get("eta_hours")
    eta_str = f"ETA ~{eta}h" if eta else "Sin llegada proyectada"
    popup_html = (
        f"<b>{zone['name']}</b><br>"
        f"Riesgo: <b style='color:{color}'>{risk.upper()}</b><br>"
        f"{eta_str}"
    )
    folium.CircleMarker(
        location=[zone["center_lat"], zone["center_lon"]],
        radius=14,
        color=color,
        fill=True,
        fill_color=color,
        fill_opacity=0.6,
        popup=folium.Popup(popup_html, max_width=200),
        tooltip=f"{zone['name']}: {risk.upper()}",
    ).add_to(m)

st_folium(m, width="100%", height=520)

# ---------------------------------------------------------------------------
# Risk legend
# ---------------------------------------------------------------------------

st.markdown(
    """
    **Leyenda:**
    🔴 Alto &nbsp; 🟠 Medio &nbsp; 🟢 Bajo &nbsp; ⚪ Ninguno
    """
)
