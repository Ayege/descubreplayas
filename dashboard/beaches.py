"""Streamlit beach explorer: a friendly map + info cards for popular DR beaches.

Runs fully offline from the local dataset (dashboard/beaches_data.py).

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
    st.caption("Tip: click a beach marker on the map to load its full details on the right.")


# Always attempt live risk — fails silently when API is unreachable or not configured.
zones: list[dict] = []
risk_by_zone_id: dict[int, str] = {}
zones, risk_by_zone_id = fetch_live_risk(API_BASE_URL)
if not zones:
    st.sidebar.caption("🌊 Sargassum risk unavailable (API offline or not configured)")


def _beach_risk(beach: dict):
    """Return (risk_level, zone, distance_km), or ('none', None, inf) when no data."""
    if not zones:
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
filtered_names = {b["name"] for b in filtered}

st.markdown(f"**{len(filtered)}** of **{len(BEACHES)}** beaches match your filters.")


# ---------------------------------------------------------------------------
# Map (full-width)
# ---------------------------------------------------------------------------

m = folium.Map(location=[19.0, -69.8], zoom_start=7, tiles="CartoDB positron")
cluster = MarkerCluster().add_to(m)

for b in filtered:
    color = REGION_COLORS.get(b["region"], "#1f77b4")
    risk_level, near_zone, dist_km_b = _beach_risk(b)
    if risk_level is not None:
        color = RISK_COLORS.get(risk_level, color)
    turtle = " 🐢" if b["protected_area"] else ""
    desc_short = (b["description"] or "")[:140].rstrip()
    if len(b["description"] or "") > 140:
        desc_short += "…"
    if risk_level is not None and near_zone:
        risk_badge_color = RISK_COLORS.get(risk_level, "#6c757d")
        risk_html = (
            f"<div style='background:{risk_badge_color};color:#fff;display:inline-block;"
            f"padding:2px 8px;border-radius:4px;font-size:11px;margin:4px 0'>"
            f"🌊 Sargazo: {risk_level.upper()} · {near_zone['name']} (~{dist_km_b:.0f} km)"
            f"</div><br>"
        )
    else:
        risk_html = (
            "<div style='background:#6c757d;color:#fff;display:inline-block;"
            "padding:2px 8px;border-radius:4px;font-size:11px;margin:4px 0'>"
            "🌊 Sargazo: sin datos"
            "</div><br>"
        )
    popup_html = (
        f"<div style='font-family:sans-serif;min-width:240px;max-width:300px'>"
        f"<b style='font-size:15px'>{b['name']}{turtle}</b><br>"
        f"<span style='color:#555;font-size:12px'>{b['province']} · {b['region']}</span><br>"
        f"{risk_html}"
        f"<p style='font-size:12px;margin:4px 0'>{desc_short}</p>"
        f"<hr style='margin:4px 0'>"
        f"<span style='font-size:12px'>"
        f"🗓️ <b>Best time:</b> {b['best_time_to_visit']}<br>"
        f"🎟️ <b>Entrance:</b> {b['entrance_fee']}<br>"
        f"🌊 <b>Water:</b> {b['water_conditions']}<br>"
        f"🏄 <b>Activities:</b> {', '.join(b['activities'][:4])}<br>"
        f"🐠 <b>Wildlife:</b> {', '.join(b['wildlife'][:3]) if b['wildlife'] else 'N/A'}<br>"
        f"🏗️ <b>Facilities:</b> {', '.join(b['facilities'][:3])}<br>"
        f"</span>"
        f"<a href='{b['google_maps_url']}' target='_blank' style='font-size:12px'>📍 Open in Google Maps ↗</a>"
        f"</div>"
    )
    folium.CircleMarker(
        location=[b["latitude"], b["longitude"]],
        radius=9,
        color=color,
        fill=True,
        fill_color=color,
        fill_opacity=0.85,
        weight=2,
        popup=folium.Popup(popup_html, max_width=310),
        tooltip=f"{b['name']} — {b['province']}",
    ).add_to(cluster)

map_result = st_folium(m, width="100%", height=620, returned_objects=["last_object_clicked_tooltip"])

# Sync map click -> detail section via session state.
if map_result and map_result.get("last_object_clicked_tooltip"):
    tooltip_text: str = map_result["last_object_clicked_tooltip"]
    clicked_name = tooltip_text.split(" — ")[0] if " — " in tooltip_text else tooltip_text
    if clicked_name in filtered_names:
        st.session_state["selected_beach"] = clicked_name

# Legend
if zones:
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

st.divider()

# ---------------------------------------------------------------------------
# Detail panel (below full-width map)
# ---------------------------------------------------------------------------

if not filtered:
    st.info("No beaches match the current filters. Try widening them.")
else:
    names = [b["name"] for b in sorted(filtered, key=lambda x: x["name"])]
    default_name = st.session_state.get("selected_beach")
    if default_name not in names:
        default_name = names[0]
    default_idx = names.index(default_name)
    chosen = st.selectbox("📍 Select a beach for full details", names, index=default_idx)
    st.session_state["selected_beach"] = chosen
    beach = next(b for b in filtered if b["name"] == chosen)

    turtle = " 🐢" if beach["protected_area"] else ""
    risk_level, near_zone, dist_km = _beach_risk(beach)

    # Risk banner
    if risk_level is not None and near_zone:
        banner_color = RISK_COLORS.get(risk_level, "#6c757d")
        st.markdown(
            f"<div style='background:{banner_color};color:#fff;padding:8px 14px;"
            f"border-radius:6px;font-size:14px;margin-bottom:8px'>"
            f"🌊 <b>Sargassum risk:</b> {RISK_EMOJI.get(risk_level, '')} {risk_level.upper()} "
            f"— nearest zone <b>{near_zone['name']}</b> (~{dist_km:.0f} km away)"
            f"</div>",
            unsafe_allow_html=True,
        )

    col_title, col_btn = st.columns([5, 1])
    with col_title:
        st.subheader(f"{beach['name']}{turtle}")
        st.caption(f"{beach['province']} · {beach['region']}")
    with col_btn:
        st.link_button("📍 Maps", beach["google_maps_url"])

    st.write(beach["description"])

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**🗓️ Best time**")
        st.write(beach["best_time_to_visit"])
        st.markdown("**🚪 Access**")
        st.write(beach["access_type"])
        st.caption(beach["access_description"])
    with c2:
        st.markdown("**🎟️ Entrance**")
        st.write(beach["entrance_fee"])
        st.markdown("**🅿️ Parking**")
        st.write("Yes" if beach["parking"] else "No / limited")
        st.markdown("**🌊 Water**")
        st.write(beach["water_conditions"])
    with c3:
        st.markdown("**🏄 Activities**")
        st.write(", ".join(beach["activities"]))
        st.markdown("**🐠 Wildlife**")
        st.write(", ".join(beach["wildlife"]) if beach["wildlife"] else "N/A")
        st.markdown("**🏗️ Facilities**")
        st.write(", ".join(beach["facilities"]))

    st.markdown(f"**🌿 Ecosystem:** {beach['ecosystem']}")

    # -----------------------------------------------------------------------
    # Recommendations
    # -----------------------------------------------------------------------
    st.divider()
    st.subheader("✨ You might also like")

    def _score(candidate: dict) -> int:
        """Higher = more similar to the selected beach."""
        score = 0
        if candidate["region"] == beach["region"]:
            score += 3
        if candidate["province"] == beach["province"]:
            score += 2
        shared_activities = set(candidate["activities"]) & set(beach["activities"])
        score += len(shared_activities)
        if candidate["protected_area"] == beach["protected_area"]:
            score += 1
        return score

    recs = sorted(
        [b for b in BEACHES if b["name"] != beach["name"]],
        key=_score,
        reverse=True,
    )[:3]

    rec_cols = st.columns(3)
    for col, rec in zip(rec_cols, recs):
        rec_risk_level, rec_zone, _d = _beach_risk(rec)
        rec_color = RISK_COLORS.get(rec_risk_level, "#6c757d") if rec_risk_level else "#6c757d"
        rec_turtle = " 🐢" if rec["protected_area"] else ""
        with col:
            st.markdown(
                f"<div style='border:1px solid #ddd;border-radius:8px;padding:12px'>"
                f"<b style='font-size:14px'>{rec['name']}{rec_turtle}</b><br>"
                f"<span style='color:#666;font-size:12px'>{rec['province']}</span><br>"
                + (
                    f"<span style='background:{rec_color};color:#fff;border-radius:4px;"
                    f"padding:1px 6px;font-size:11px'>🌊 {rec_risk_level.upper()}</span><br>"
                    if rec_risk_level else ""
                )
                + f"<span style='font-size:12px'>🗓️ {rec['best_time_to_visit']}<br>"
                f"🏄 {', '.join(rec['activities'][:3])}</span><br>"
                f"</div>",
                unsafe_allow_html=True,
            )
            if st.button("View details", key=f"rec_{rec['name']}"):
                st.session_state["selected_beach"] = rec["name"]
                st.rerun()
