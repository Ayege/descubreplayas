"""Streamlit beach explorer: a friendly map + info cards for popular DR beaches.

Runs fully offline from the local dataset (dashboard/beaches_data.py).

Run from repo root:
    streamlit run dashboard/beaches.py
"""
from __future__ import annotations

import os

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
    page_title="Playas RD — DR Beach Explorer",
    page_icon="🏖️",
    layout="wide",
)

BEACHES = beaches_with_maps()


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.title("🏖️ Playas RD — Dominican Republic Beach Explorer")
st.caption(
    "Explore the country's most beautiful beaches: access, best time to visit, "
    "activities, wildlife, and facilities — all on one friendly map."
)


# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("🔎 Filters")

    sel_regions = st.multiselect("Region", all_regions(), default=all_regions())
    sel_provinces = st.multiselect("Province", all_provinces(), default=[])
    sel_activities = st.multiselect("Activity", all_activities(), default=[])
    protected_only = st.checkbox("Protected areas only 🐢", value=False)
    free_only = st.checkbox("Free entrance only", value=False)

    st.divider()
    show_risk = st.checkbox("🌊 Show live sargassum risk", value=False)
    st.caption(
        "When on, each beach is coloured by the current sargassum risk of its "
        "nearest monitored zone (live from the API)."
    )

    st.divider()
    st.caption("Tip: click a marker on the map for a quick summary, or pick a beach below for full details.")


# Fetch live risk only when requested (keeps the app fast/offline by default).
zones: list[dict] = []
risk_by_zone_id: dict[int, str] = {}
if show_risk:
    zones, risk_by_zone_id = fetch_live_risk(API_BASE_URL)
    if not zones:
        st.sidebar.warning("Could not reach the risk API — showing region colours instead.")


def _beach_risk(beach: dict):
    """Return (risk_level, zone, distance_km) or (None, None, None) if disabled."""
    if not (show_risk and zones):
        return None, None, None
    return risk_for_beach(beach, zones, risk_by_zone_id)


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

st.markdown(f"**{len(filtered)}** of **{len(BEACHES)}** beaches match your filters.")


# ---------------------------------------------------------------------------
# Map
# ---------------------------------------------------------------------------

col_map, col_detail = st.columns([3, 2], gap="large")

with col_map:
    m = folium.Map(location=[19.0, -69.8], zoom_start=7, tiles="CartoDB positron")
    cluster = MarkerCluster().add_to(m)

    for b in filtered:
        color = REGION_COLORS.get(b["region"], "#1f77b4")
        risk_level, near_zone, _dist = _beach_risk(b)
        if risk_level is not None:
            color = RISK_COLORS.get(risk_level, color)
        turtle = " 🐢" if b["protected_area"] else ""
        risk_line = (
            f"<b>Sargassum risk:</b> {RISK_EMOJI.get(risk_level, '')} {risk_level.upper()}"
            f" (nearest: {near_zone['name']})<br>"
            if risk_level is not None and near_zone
            else ""
        )
        popup_html = (
            f"<div style='font-family:sans-serif; min-width:200px'>"
            f"<b style='font-size:14px'>{b['name']}{turtle}</b><br>"
            f"<span style='color:#666'>{b['province']}</span><br>"
            f"{risk_line}"
            f"<b>Best time:</b> {b['best_time_to_visit']}<br>"
            f"<b>Access:</b> {b['access_type']} · {b['entrance_fee']}<br>"
            f"<b>Top activities:</b> {', '.join(b['activities'][:3])}<br>"
            f"<a href='{b['google_maps_url']}' target='_blank'>Open in Google Maps ↗</a>"
            f"</div>"
        )
        folium.CircleMarker(
            location=[b["latitude"], b["longitude"]],
            radius=8,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.85,
            weight=2,
            popup=folium.Popup(popup_html, max_width=260),
            tooltip=f"{b['name']} — {b['province']}",
        ).add_to(cluster)

    st_folium(m, width="100%", height=560, returned_objects=[])

    # Legend switches between risk and region depending on the toggle.
    if show_risk and zones:
        legend_items = " &nbsp; ".join(
            f"<span style='color:{color}'>●</span> {level.capitalize()}"
            for level, color in RISK_COLORS.items()
        )
        st.markdown(
            f"<div style='font-size:12px'><b>Sargassum risk:</b> {legend_items}</div>",
            unsafe_allow_html=True,
        )
    else:
        legend_items = " &nbsp; ".join(
            f"<span style='color:{color}'>●</span> {region}"
            for region, color in REGION_COLORS.items()
        )
        st.markdown(f"<div style='font-size:12px'>{legend_items}</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Detail panel
# ---------------------------------------------------------------------------

with col_detail:
    if not filtered:
        st.info("No beaches match the current filters. Try widening them.")
    else:
        names = [b["name"] for b in sorted(filtered, key=lambda x: x["name"])]
        chosen = st.selectbox("📍 Beach details", names)
        beach = next(b for b in filtered if b["name"] == chosen)

        turtle = " 🐢" if beach["protected_area"] else ""
        st.subheader(f"{beach['name']}{turtle}")
        st.caption(f"{beach['province']} · {beach['region']}")
        st.write(beach["description"])

        risk_level, near_zone, dist_km = _beach_risk(beach)
        if risk_level is not None and near_zone:
            st.markdown(
                f"**🌊 Live sargassum risk:** {RISK_EMOJI.get(risk_level, '')} "
                f"`{risk_level.upper()}` — nearest zone **{near_zone['name']}** "
                f"(~{dist_km:.0f} km)"
            )

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**🗓️ Best time**")
            st.write(beach["best_time_to_visit"])
            st.markdown("**🚪 Access**")
            st.write(f"{beach['access_type']}")
            st.caption(beach["access_description"])
            st.markdown("**🎟️ Entrance**")
            st.write(beach["entrance_fee"])
            st.markdown("**🅿️ Parking**")
            st.write("Yes" if beach["parking"] else "No / limited")
        with c2:
            st.markdown("**🏄 Activities**")
            st.write(", ".join(beach["activities"]))
            st.markdown("**🌊 Water**")
            st.write(beach["water_conditions"])
            st.markdown("**🐠 Wildlife**")
            st.write(", ".join(beach["wildlife"]))
            st.markdown("**🏗️ Facilities**")
            st.write(", ".join(beach["facilities"]))

        st.markdown("**🌿 Ecosystem**")
        st.write(beach["ecosystem"])
        st.link_button("Open in Google Maps ↗", beach["google_maps_url"])


# ---------------------------------------------------------------------------
# Full table (expandable)
# ---------------------------------------------------------------------------

with st.expander("📋 Browse all beaches as a table"):
    table_rows = [
        {
            "Beach": b["name"],
            "Province": b["province"],
            "Region": b["region"],
            "Best time": b["best_time_to_visit"],
            "Access": b["access_type"],
            "Entrance": b["entrance_fee"],
            "Protected": "🐢" if b["protected_area"] else "",
        }
        for b in sorted(filtered, key=lambda x: (x["region"], x["name"]))
    ]
    st.dataframe(table_rows, use_container_width=True, hide_index=True)
