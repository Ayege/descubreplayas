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

"""Render layer for "Conoce esta playa": builds the panel's HTML, nothing else.

All markup goes through _el/_void. A plain str child or attribute value is
always encoded; only _Html, which those helpers return, passes through as is.
So forgetting to escape something cannot happen by accident: raw interpolation
of a value into markup does not appear in this file.

The panel is a closed <details> at the bottom of the card, in muted colours, so
it stays secondary to the sargassum risk at the top.
"""
from __future__ import annotations

import html
import logging
import re
from collections.abc import Iterable, Mapping
from functools import lru_cache
from types import MappingProxyType
from typing import NamedTuple

import streamlit as st

from dashboard.icons import icon
from dashboard.conoce_data import (
    BEACH_IMAGE_FILE_RE,
    DEFAULT_LANGUAGE,
    LANGUAGES,
    MAX_FEATURED_SPECIES,
    PHOTO_LICENSES,
    SPECIES_IMAGE_FILE_RE,
    BeachKnowledge,
    BeachPhoto,
    HistoriaItem,
    Localized,
    Photo,
    Roca,
    Sourced,
    Species,
    is_safe_url,
    knowledge_for,
)

logger = logging.getLogger(__name__)

# The _T keys this panel needs, in both languages (checked by the tests).
LABEL_KEYS = (
    "ecosystem",
    "wildlife",
    "conoce_title",
    "lens_vida",
    "lens_roca",
    "lens_historia",
    "see_more",
    "source",
    "sources",
    "photos_credit",
    "sand",
    "sargassum_why",
    "sargassum_note",
    "reference_photo",
    "photo_by",
    "licence",
)

# Absolute, because a relative src breaks on pretty URLs such as /beach/<name>.
STATIC_PREFIX = "/app/static"
THUMBNAIL_PX = 48
# Line icon shown for a species with no allowed photo (names from dashboard.icons).
# It is decorative: the species name beside it carries the meaning, so it is
# hidden from screen readers.
ICONIC_GROUP_ICON = MappingProxyType({
    "Aves": "bird",
    "Actinopterygii": "fish",
    "Reptilia": "turtle",
    "Mollusca": "shell",
    "Animalia": "paw-print",
    "Plantae": "leaf",
    "Chromista": "leaf",
    "Insecta": "bug",
    "Arachnida": "bug",
    "Amphibia": "paw-print",
    "Fungi": "sprout",
})
DEFAULT_GROUP_ICON = "paw-print"
# Icon beside the panel title. The lens titles stay plain text: the panel has a
# byte budget, and an icon on every header added weight without adding meaning.
TITLE_ICON = "compass"
LINK_REL = "noopener noreferrer"
SEPARATOR = " · "
LENS_GROUP = "conoce-lens"
# The panel itself starts expanded. Set to False to go back to a closed panel, which
# loads no images until it is opened.
PANEL_STARTS_OPEN = True
_WHITESPACE_RE = re.compile(r"\s+")

# The card's own palette. Muted text uses the secondary colour because the
# darker #546e7a scores only 1.2-3.2:1 on the card's gradient.
_COLOR_LABEL = "#80cbc4"
_COLOR_BODY = "#e0f7fa"
_COLOR_SECONDARY = "#b2dfdb"
_COLOR_ITEM_BG = "rgba(0,0,0,.2)"
_COLOR_THUMB_BG = "rgba(255,255,255,.08)"

_SUMMARY_CSS = (
    f"color:{_COLOR_LABEL};font-size:11px;font-weight:700;cursor:pointer;"
    "text-transform:uppercase;letter-spacing:.5px"
)
_CSS = (
    ".conoce{margin-top:10px;overflow-wrap:anywhere}"
    f".conoce summary{{{_SUMMARY_CSS}}}"
    f".conoce summary:focus-visible{{outline:2px solid {_COLOR_LABEL};outline-offset:2px;border-radius:4px}}"
    f".conoce a{{color:{_COLOR_SECONDARY}}}"
    f".conoce-tease{{display:block;margin-top:3px;color:{_COLOR_SECONDARY};font-weight:400;"
    "text-transform:none;letter-spacing:0;line-height:1.35}"
    f".conoce-lens{{margin-top:6px;padding:0 8px;background:{_COLOR_ITEM_BG};border-radius:8px}}"
    ".conoce-lens>summary{padding:8px 0}"
    ".conoce-lens[open]{padding-bottom:8px}"
    ".conoce-item{margin:0 0 10px}"
    ".conoce-item:last-child{margin-bottom:0}"
    ".conoce-row{display:flex;gap:8px}"
    ".conoce-row>div{min-width:0}"
    f".conoce-row img{{flex:none;border-radius:8px;background:{_COLOR_THUMB_BG}}}"
    f".conoce-icon{{flex:none;display:flex;align-items:center;justify-content:center;width:{THUMBNAIL_PX}px;"
    f"height:{THUMBNAIL_PX}px;border-radius:8px;background:{_COLOR_ITEM_BG};color:{_COLOR_LABEL}}}"
    ".conoce summary .ic{margin-right:5px}"
    f".conoce-label{{color:{_COLOR_LABEL};font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px}}"
    f".conoce-text{{color:{_COLOR_BODY};font-size:12px;line-height:1.4}}"
    ".conoce-name{font-weight:700}"
    f".conoce-sci{{color:{_COLOR_SECONDARY};font-size:11px;font-style:italic}}"
    f".conoce-fine{{margin-top:2px;color:{_COLOR_SECONDARY};font-size:9.5px;line-height:1.35}}"
    ".conoce-photo{margin:8px 0 0}"
    ".conoce-photo img{display:block;max-width:100%;height:auto;border-radius:8px}"
    ".conoce-more>summary{text-transform:none;letter-spacing:0;padding:2px 0 6px}"
    "@media (pointer:coarse){.conoce>summary,.conoce-lens>summary{padding:13px 0}}"
)


class _Catalog(NamedTuple):
    """Translated catalogue text shown in the Vida lens."""

    ecosystem: str
    wildlife: str


class _Html(str):
    """Markup that is already encoded. Only _el and _void should create it."""


def _encode(value: str) -> str:
    """Escape text and collapse each run of whitespace to one space.

    Collapsing matters here: a blank line inside st.markdown ends the raw HTML
    block, and whatever follows would be parsed as markdown. Edge spaces are
    kept because separators such as " · " depend on them.
    """
    return html.escape(_WHITESPACE_RE.sub(" ", value), quote=True)


def _attrs(attrs: Mapping[str, str | int] | None) -> str:
    return "".join(f' {name}="{_encode(str(value))}"' for name, value in (attrs or {}).items())


def _join(children: Iterable[str]) -> _Html:
    return _Html("".join(c if isinstance(c, _Html) else _encode(c) for c in children))


def _el(tag: str, attrs: Mapping[str, str | int] | None, *children: str) -> _Html:
    return _Html(f"<{tag}{_attrs(attrs)}>{_join(children)}</{tag}>")


def _void(tag: str, attrs: Mapping[str, str | int]) -> _Html:
    return _Html(f"<{tag}{_attrs(attrs)}>")


@lru_cache(maxsize=256)
def _log_dropped_url(kind: str, url: str) -> None:
    """Warn once per URL. The cache is what makes it once."""
    logger.warning("conoce: dropped unsafe %s URL %r", kind, url)


def _checked(kind: str, url: object) -> bool:
    if is_safe_url(url):
        return True
    _log_dropped_url(kind, str(url))
    return False


def _link(url: str, text: str, title: str | None = None) -> _Html:
    attrs = {"href": url, "target": "_blank", "rel": LINK_REL}
    if title:
        attrs["title"] = title
    return _el("a", attrs, text)


def _sources_line(record: Sourced, labels: Mapping[str, str]) -> _Html:
    """A tiny "Fuente" link, or "Fuentes: 1 · 2" when there are several."""
    sources = [s for s in record["sources"] if _checked("source", s["url"])]
    if not sources:
        return _Html("")
    if len(sources) == 1:
        only = sources[0]
        return _el("div", {"class": "conoce-fine"}, _link(only["url"], labels["source"], only["label"]))
    parts: list[str] = [f"{labels['sources']}: "]
    for number, source in enumerate(sources, start=1):
        if number > 1:
            parts.append(SEPARATOR)
        parts.append(_link(source["url"], str(number), source["label"]))
    return _el("div", {"class": "conoce-fine"}, *parts)


def _species_text(species: Species, lang: str, labels: Mapping[str, str]) -> list[str]:
    parts: list[str] = [
        _el("div", {"class": "conoce-text conoce-name"}, species["common_name"][lang]),
        _el("div", {"class": "conoce-sci"}, species["scientific_name"]),
    ]
    if species["blurb"]:
        parts.append(_el("div", {"class": "conoce-text"}, species["blurb"][lang]))
    if species["sargassum_note"]:
        parts.append(_el(
            "div", {"class": "conoce-fine"},
            f"{labels['sargassum_note']}: ",
            species["sargassum_note"][lang],
        ))
    parts.append(_sources_line(species, labels))
    return parts


def _static_src(file: object, pattern: re.Pattern[str]) -> str | None:
    """The image's URL, only ever our prefix plus a file name of hash characters."""
    if isinstance(file, str) and pattern.fullmatch(file):
        return f"{STATIC_PREFIX}/{file}"
    _log_dropped_url("image", str(file))
    return None


def _thumbnail(species: Species) -> _Html:
    """The self-hosted photo if it passes, or nothing.

    The alt text is empty because the species name sits right beside it: a
    screen reader would otherwise say it twice, and a failed image would show
    the name as stray text.
    """
    photo = species["photo"]
    if not photo or photo["license_code"] not in PHOTO_LICENSES:
        return _Html("")
    src = _static_src(photo["file"], SPECIES_IMAGE_FILE_RE)
    if src is None:
        return _Html("")
    return _void("img", {
        "src": src,
        "alt": "",
        "width": THUMBNAIL_PX,
        "height": THUMBNAIL_PX,
        "loading": "lazy",
        "decoding": "async",
    })


def _icon(name: str, size: int = 12) -> _Html:
    return _Html(icon(name, size))


def _group_icon(species: Species) -> _Html:
    name = ICONIC_GROUP_ICON.get(species["iconic_group"], DEFAULT_GROUP_ICON)
    return _el("span", {"class": "conoce-icon", "aria-hidden": "true"}, _icon(name, 22))


def _photo_credit(photo: Photo, labels: Mapping[str, str]) -> _Html:
    """Name the creator and license, without implying where the photo was taken."""
    license_label, license_url = PHOTO_LICENSES[photo["license_code"]]
    attribution: str = photo["attribution"]
    if _checked("photo source", photo["source_url"]):
        attribution = _link(photo["source_url"], photo["attribution"])
    return _el(
        "div", {"class": "conoce-fine"},
        f"{labels['photos_credit']}: ",
        attribution,
        SEPARATOR,
        _link(license_url, license_label),
    )


def _featured_row(species: Species, lang: str, labels: Mapping[str, str]) -> _Html:
    thumbnail = _thumbnail(species)
    text = _species_text(species, lang, labels)
    if thumbnail:
        text.append(_photo_credit(species["photo"], labels))
    picture = thumbnail or _group_icon(species)
    return _el("div", {"class": "conoce-item conoce-row"}, picture, _el("div", None, *text))


def _photo_caption(photo: BeachPhoto, labels: Mapping[str, str]) -> _Html:
    """Label, year if the source gives one, author, licence and source.

    The link uses the photo's own licence_url, because Commons files have several
    licence versions and PHOTO_LICENSES only holds the latest deed.
    """
    parts: list[str] = [labels["reference_photo"]]
    if photo["taken_year"] is not None:
        parts += [SEPARATOR, str(photo["taken_year"])]
    parts += [SEPARATOR, _el("span", {"title": labels["photo_by"]}, photo["author"])]
    parts += [SEPARATOR, _link(photo["license_url"], PHOTO_LICENSES[photo["license"]][0], labels["licence"])]
    parts += [SEPARATOR, _link(photo["source_url"], labels["source"])]
    return _el("figcaption", {"class": "conoce-fine"}, *parts)


def _beach_photo(photo: BeachPhoto | None, lang: str, labels: Mapping[str, str]) -> _Html:
    """The reference photo, or nothing if any part of its credit can't be shown."""
    if not photo:
        return _Html("")
    src = _static_src(photo["file"], BEACH_IMAGE_FILE_RE)
    alt = photo["alt"][lang].strip()
    usable = (
        src is not None
        and alt
        and photo["license"] in PHOTO_LICENSES
        and _checked("licence", photo["license_url"])
        and _checked("photo source", photo["source_url"])
    )
    if not usable:
        return _Html("")
    image = _void("img", {
        "src": src,
        "alt": alt,
        "width": photo["width"],
        "height": photo["height"],
        "loading": "lazy",
        "decoding": "async",
    })
    return _el("figure", {"class": "conoce-photo"}, image, _photo_caption(photo, labels))


def _catalog_line(label: str, value: str) -> _Html:
    """A line of DescubrePlayas editorial data, laid out like the card's rows."""
    return _el(
        "div", {"class": "conoce-item"},
        _el("div", {"class": "conoce-label"}, label),
        _el("div", {"class": "conoce-text"}, value),
    )


def _vida_body(
    knowledge: BeachKnowledge | None,
    lang: str,
    labels: Mapping[str, str],
    catalog: _Catalog,
) -> _Html:
    """Ecosystem first, then species; the wildlife list only if there are none."""
    species = knowledge["species"] if knowledge else []
    featured, more = species[:MAX_FEATURED_SPECIES], species[MAX_FEATURED_SPECIES:]
    parts: list[str] = []
    if catalog.ecosystem:
        parts.append(_catalog_line(labels["ecosystem"], catalog.ecosystem))
    if catalog.wildlife and not featured:
        parts.append(_catalog_line(labels["wildlife"], catalog.wildlife))
    parts += [_featured_row(species, lang, labels) for species in featured]
    if more:
        rows = [_el("div", {"class": "conoce-item"}, *_species_text(s, lang, labels)) for s in more]
        summary = _el("summary", None, labels["see_more"].format(n=len(more)))
        parts.append(_el("details", {"class": "conoce-more"}, summary, *rows))
    return _join(parts)


def _roca_body(knowledge: BeachKnowledge | None, lang: str, labels: Mapping[str, str]) -> _Html:
    roca: Roca | None = knowledge["roca"] if knowledge else None
    if not roca:
        return _Html("")
    parts: list[str] = [
        _el("div", {"class": "conoce-text conoce-name"}, roca["title"][lang]),
        _el("div", {"class": "conoce-text"}, roca["story"][lang]),
        _el("div", {"class": "conoce-text"}, f"{labels['sand']}: ", roca["sand"][lang]),
    ]
    if knowledge["sargazo_por_que"]:
        parts.append(_el("div", {"class": "conoce-label"}, labels["sargassum_why"]))
        parts.append(_el("div", {"class": "conoce-text"}, knowledge["sargazo_por_que"][lang]))
    parts.append(_sources_line(roca, labels))
    return _el("div", {"class": "conoce-item"}, *parts)


def _history_item(item: HistoriaItem, lang: str, labels: Mapping[str, str]) -> _Html:
    parts: list[str] = []
    if item["year"] is not None:
        parts.append(_el("div", {"class": "conoce-label"}, str(item["year"])))
    parts += [
        _el("div", {"class": "conoce-text conoce-name"}, item["title"][lang]),
        _el("div", {"class": "conoce-text"}, item["body"][lang]),
        _sources_line(item, labels),
    ]
    return _el("div", {"class": "conoce-item"}, *parts)


def _historia_body(items: list[HistoriaItem], lang: str, labels: Mapping[str, str]) -> _Html:
    return _join(_history_item(item, lang, labels) for item in items)


def _lenses(bodies: Iterable[tuple[str, _Html]]) -> list[_Html]:
    """Non-empty lenses as an exclusive accordion. The first starts open, so
    opening the panel shows content straight away instead of asking for a
    second tap.
    """
    lenses: list[_Html] = []
    for title, body in bodies:
        if body:
            attrs = {"class": "conoce-lens", "name": LENS_GROUP}
            if not lenses:
                attrs["open"] = "open"
            lenses.append(_el("details", attrs, _el("summary", None, title), body))
    return lenses


def _summary(teaser_text: Localized | None, lang: str, labels: Mapping[str, str]) -> _Html:
    children: list[str] = [_icon(TITLE_ICON), labels["conoce_title"]]
    if teaser_text:
        children.append(_el("span", {"class": "conoce-tease"}, teaser_text[lang]))
    return _el("summary", None, *children)


def render_conoce_html(
    knowledge: BeachKnowledge | None,
    lang: str,
    labels: Mapping[str, str],
    ecosystem: str = "",
    wildlife: str = "",
) -> str:
    """Build the panel for one beach; "" when it has neither a photo nor a lens.

    ecosystem and wildlife are the catalogue's text, already translated.
    Pure: no network or disk access, and it does not need a Streamlit runtime.
    """
    lang = lang if lang in LANGUAGES else DEFAULT_LANGUAGE
    teaser = knowledge["teaser"] if knowledge else None
    lenses = _lenses((
        (labels["lens_vida"], _vida_body(knowledge, lang, labels, _Catalog(ecosystem, wildlife))),
        (labels["lens_roca"], _roca_body(knowledge, lang, labels)),
        (labels["lens_historia"], _historia_body(knowledge["historia"] if knowledge else [], lang, labels)),
    ))
    photo = _beach_photo(knowledge["photo"] if knowledge else None, lang, labels)
    if not lenses and not photo:
        return ""
    summary = _summary(teaser["text"] if teaser else None, lang, labels)
    attrs = {"class": "conoce", "open": "open"} if PANEL_STARTS_OPEN else {"class": "conoce"}
    return str(_el("details", attrs, summary, photo, *lenses))


@st.cache_data(show_spinner=False)
def _cached_panel(
    beach_name: str,
    lang: str,
    _labels: Mapping[str, str],
    _ecosystem: str,
    _wildlife: str,
) -> str:
    """Cache the finished HTML per (beach, lang).

    st.cache_data copies its value on every hit, so a rerun here pays only for
    one short string. Caching the whole index this way would copy all the beaches
    on each rerun; see load_index. The leading underscore keeps _labels out of
    the cache key: it is fixed for each lang, and hashing it would be wasted work.
    _ecosystem and _wildlife are unhashed for the same reason: each is fixed for
    a (beach, lang) pair.
    """
    return render_conoce_html(knowledge_for(beach_name), lang, _labels, _ecosystem, _wildlife)


def conoce_panel(
    beach_name: str,
    lang: str,
    labels: Mapping[str, str],
    ecosystem: str = "",
    wildlife: str = "",
) -> str:
    """The panel for the beach card. On any failure it is hidden, never raised:
    this sits on the same card as the risk reading, which must always render.

    The CSS goes in its own st.markdown block, never in the returned HTML:
    beaches.py hides every Streamlit container holding a <style> tag, so a style
    tag inside the card would hide the whole card, risk reading included.
    """
    try:
        panel = _cached_panel(beach_name, lang, labels, ecosystem, wildlife)
        if panel:
            st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)
        return panel
    except (AttributeError, KeyError, TypeError, ValueError):
        logger.exception("conoce: could not render the panel for %r", beach_name)
        return ""
