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

"""Offline tests for the "Conoce esta playa" panel: markup, security and budgets.

Run from the repo root:

    python -m tests.test_conoce
"""
from __future__ import annotations

import ast
import builtins
import logging
import re
import socket
import statistics
import sys
import timeit
from pathlib import Path
from unittest import mock

from dashboard import conoce_data as cd
from dashboard.beaches_data import BEACHES
from dashboard.icons import ICON_NAMES
from dashboard.conoce import (
    DEFAULT_GROUP_ICON, ICONIC_GROUP_ICON, LABEL_KEYS, LENS_GROUP, STATIC_PREFIX, conoce_panel, render_conoce_html,
)
from tests.conoce_fixtures import (
    BEACH, LABELS, UNIT, XSS, auto, beach_photo, full_inputs, historia, loc, note, profile, sourced, taxon, teaser, unit, vida,
)

_REPO = Path(__file__).resolve().parent.parent
_BEACHES_PY = _REPO / "dashboard" / "beaches.py"
_MAX_PANEL_BYTES = 12 * 1024
_MAX_RENDER_MS = 5.0
_RENDER_RUNS = 100


def _check(condition: bool, message: str) -> None:
    assert condition, message


def _knowledge(raw: dict | None = None, units: dict | None = None, data: dict | None = None) -> dict:
    profiles = {BEACH: raw} if raw else {}
    return cd.build_index(profiles, {UNIT: unit()} if units is None else units, data or {})[BEACH]


def _full_knowledge() -> dict:
    return cd.build_index(*full_inputs())[BEACH]


def _render(knowledge: dict | None, lang: str = "es", ecosystem: str = "", wildlife: str = "") -> str:
    return render_conoce_html(knowledge, lang, LABELS, ecosystem, wildlife)


def test_hides_empty_lenses() -> None:
    _check(_render(None) == "", "no knowledge and no catalogue text means no panel")
    only_teaser = _knowledge(profile(teaser=teaser()), {UNIT: unit(with_roca=False)})
    _check(_render(only_teaser) == "", "a teaser alone means no panel")
    only_roca = _render(_knowledge(profile()))
    _check("Roca" in only_roca and "Vida" not in only_roca and "Historia" not in only_roca, "only Roca should show")
    for text in ("N/A", "Sin datos"):
        _check(text not in only_roca, f"never print {text}")


def test_vida_lens_uses_catalogue_text() -> None:
    plain = _render(None, ecosystem="Arrecife", wildlife="Pelicanos")
    _check("Arrecife" in plain and "Pelicanos" in plain, "no species: both catalogue rows show")
    _check(plain.index("Arrecife") < plain.index("Pelicanos"), "ecosystem must come first")
    knowledge = _knowledge(profile(), data=auto(taxon(1)))
    with_species = _render(knowledge, ecosystem="Arrecife", wildlife="Pelicanos")
    _check("Pelicanos" not in with_species, "wildlife shows only when there are no species")
    _check(with_species.index("Arrecife") < with_species.index("Especie 1"), "ecosystem is the first line")
    _check("<script" not in _render(None, ecosystem=XSS, wildlife=XSS), "catalogue text must be escaped")


def test_catalogue_panels_fit_budget() -> None:
    for beach in BEACHES:
        panel = _render(cd.knowledge_for(beach["name"]), ecosystem=beach["ecosystem"], wildlife=", ".join(beach["wildlife"]))
        _check(0 < len(panel.encode("utf-8")) <= _MAX_PANEL_BYTES, f"{beach['name']}: panel size")


def test_rows_left_more_details() -> None:
    source = _BEACHES_PY.read_text(encoding="utf-8")
    for label in ("wildlife", "ecosystem"):
        _check(f'L["{label}"]' not in source, f"{label} is still a card row")


def test_language() -> None:
    knowledge = _knowledge(profile(historia=[historia()]), data=auto(taxon(1)))
    spanish, english = _render(knowledge, "es"), _render(knowledge, "en")
    _check("roca es" in spanish and "rock en" not in spanish, "es panel leaked english")
    _check("rock en" in english and "roca es" not in english, "en panel leaked spanish")
    _check("Species 1" in english and "Especie 1" in spanish, "species names follow the language")
    _check("roca es" in _render(knowledge, "fr"), "unknown language falls back to es")


def test_roca_lens() -> None:
    plain = _render(_knowledge(profile()))
    _check("título es" in plain and "roca es" in plain and "Arena: arena es" in plain, "title, story and sand")
    _check("Por qué llega" not in plain, "the sargassum line is optional")
    why = _render(_knowledge(profile(), {UNIT: unit(why=True)}))
    _check("Por qué llega" in why and "por qué es" in why, "the sargassum line shows when present")
    _check(plain.count(">Fuente</a>") == 1, "the lens ends with its source")


def test_historia_lens() -> None:
    html = _render(_knowledge(profile(historia=[historia("a", 1492), historia("b", None)])))
    _check(">1492</div>" in html and "a es" in html and "cuerpo es" in html, "year, title and body")
    _check(html.count('class="conoce-label"') == 1, "no year, no label")


def test_species_row() -> None:
    raw = profile(vida=vida(notes={1: note(sargassum=True)}))
    html = _render(_knowledge(raw, data=auto(taxon(1))))
    _check("Especie 1" in html and "Testus n1" in html, "common and scientific names")
    _check("nota es" in html and "Sargazo: " in html and "sarg es" in html, "blurb and sargassum note")


def test_photo_credit_does_not_place_the_photo() -> None:
    html = _render(_knowledge(profile(), data=auto(taxon(1))))
    _check("Foto de la especie: <a " in html and "Persona de prueba" in html, "credit names the creator")
    _check(">CC BY</a>" in html, "credit names the license")
    source = (_REPO / "dashboard" / "beaches.py").read_text(encoding="utf-8")
    for line in source.splitlines():
        if '"photos_credit"' in line:
            for word in ("aquí", "here", "playa", "beach", "tomada", "taken"):
                _check(word not in line.lower(), f"the credit must not place the photo: {word}")


def test_icon_fallback() -> None:
    fish = taxon(1, group="Actinopterygii", with_photo=False)
    html = _render(_knowledge(profile(), data=auto(fish)))
    _check("<img" not in html, "no photo means no image")
    _check('<span class="conoce-icon" aria-hidden="true"><svg class="ic ic-fish"' in html, "group icon, hidden from screen readers")
    _check("Especie 1" in html, "the species name carries the meaning")
    _check("Foto de la especie" not in html, "no photo, no credit")
    for group in (None, "Grupo nuevo"):
        other = _render(_knowledge(profile(), data=auto(taxon(1, group=group, with_photo=False))))
        _check(f'class="conoce-icon" aria-hidden="true"><svg class="ic ic-{DEFAULT_GROUP_ICON}"' in other, f"unknown group falls back: {group}")
    photographed = _render(_knowledge(profile(), data=auto(taxon(1))))
    _check("conoce-icon" not in photographed.split("<style")[0] and "<img" in photographed, "a photo replaces the icon")
    bad_photo = taxon(1, group="Aves")
    bad_photo["photo"]["license_code"] = "cc-by-nc"
    dropped = _render(_knowledge(profile(), data=auto(bad_photo)))
    _check(f'ic-{ICONIC_GROUP_ICON["Aves"]}' in dropped and "<img" not in dropped, "a dropped photo falls back too")


def test_icon_mapping_is_complete_for_seen_groups() -> None:
    seen = {"Aves", "Plantae", "Insecta", "Animalia", "Reptilia", "Actinopterygii", "Arachnida", "Mollusca", "Amphibia", "Fungi", "Chromista"}
    _check(seen <= set(ICONIC_GROUP_ICON), "every iconic group seen in the real data needs an icon")
    _check(set(ICONIC_GROUP_ICON.values()) <= ICON_NAMES, "every group icon must exist in dashboard.icons")


def _poisoned_knowledge() -> dict:
    """Malicious values placed straight in the render input, skipping validation."""
    species = taxon(1)
    species["scientific_name"] = XSS
    species["common_name"] = loc(XSS, XSS)
    species["photo"]["attribution"] = XSS
    species["photo"]["source_url"] = 'https://example.org/"onmouseover="alert(1)'
    bad_image = taxon(2)
    bad_image["photo"]["file"] = 'https://evil.test/x.jpg"onerror="alert(1)'
    source = {"label": XSS, "url": "javascript:alert(1)"}
    merged = [
        {**s, "photo": s["photo"], "blurb": loc(XSS, XSS), "sargassum_note": loc(XSS, XSS), "sources": [source]}
        for s in (species, bad_image)
    ]
    for item in merged:
        item["common_name"] = {k: v or item["scientific_name"] for k, v in item["common_name"].items()}
        item["observation_count"] = 1
    roca = sourced(title=loc(XSS, XSS), story=loc(XSS + "\n\n# markdown", XSS), rock_type=XSS, sand=loc(XSS, XSS))
    return {
        "profile": None,
        "unit": None,
        "teaser": sourced(text=loc(XSS, XSS)),
        "species": merged,
        "roca": roca,
        "sargazo_por_que": loc(XSS, XSS),
        "historia": [sourced(title=loc(XSS, XSS), body=loc(XSS + "\n\nx", XSS), year=1900)],
        "photo": beach_photo() | {
            "author": XSS,
            "alt": loc(XSS + "\n\nx", XSS),
            "license_url": "https://creativecommons.org/licenses/by-sa/3.0/?a=\"onclick=\"alert(1)",
        },
    }


def test_encodes_everything() -> None:
    html = _render(_poisoned_knowledge())
    _check("<script" not in html, "unescaped script tag")
    _check("javascript:" not in html, "unsafe link rendered")
    _check("evil.test" not in html, "unsafe image rendered")
    _check('"onmouseover="' not in html and '"onerror="' not in html, "attribute breakout")
    _check("\n" not in html, "a newline can end the raw HTML block in st.markdown")


def test_separator_spaces_survive() -> None:
    html = _render(_knowledge(profile(), data=auto(taxon(1))))
    _check("</a> · <a " in html, "the photo credit separator lost its spaces")
    raw = profile(historia=[sourced(title=loc(), body=loc(), year=None, sources=[
        {"label": "a", "url": "https://example.org/a"}, {"label": "b", "url": "https://example.org/b"},
    ])])
    several = _render(_knowledge(raw))
    _check("Fuentes: <a " in several, "the sources label lost its space")
    _check(">1</a> · <a " in several, "several sources should be numbered links")


def test_links_images_and_accordion() -> None:
    html = _render(_full_knowledge())
    _check(html.count("<img") == cd.MAX_FEATURED_SPECIES, "only featured species get images")
    for attribute in ('loading="lazy"', 'decoding="async"', 'width="48"', 'height="48"', 'alt=""'):
        _check(html.count(attribute) == cd.MAX_FEATURED_SPECIES, f"image missing {attribute}")
    _check(html.count("<a ") == html.count('rel="noopener noreferrer"'), "link without rel")
    _check(html.count("<a ") == html.count('target="_blank"'), "link without target")
    _check(html.count("Persona de prueba") == cd.MAX_FEATURED_SPECIES, "photo without attribution")
    _check(f"Ver más ({cd.MAX_MORE_SPECIES})" in html, "see-more count")
    _check(html.startswith('<details class="conoce" open="open"><summary>'), "the panel starts expanded")
    _check(html.count(f'name="{LENS_GROUP}"') == 3, "the three lenses must share one accordion group")
    _check(html.count('open="open"') == 2, "the panel and the first lens start open, the other lenses closed")
    more = html.split('class="conoce-more"')[1].split('name="conoce-lens"')[0]
    _check("<img" not in more and "conoce-icon" not in more, "see-more rows must be text only")


def _photo_knowledge(*args: object, **fields: object) -> dict:
    return _knowledge(profile(), data=auto(taxon(1))) | {"photo": beach_photo(*args) | fields}


def _opening_tag(html: str, tag: str) -> str:
    return html.split(f"<{tag}", 1)[1].split(">", 1)[0]


def test_figure_is_inside_the_panel_before_the_lenses() -> None:
    html = _render(_photo_knowledge())
    _check(html.count("<figure") == 1, "one figure")
    outer_summary_end = html.index("</summary>")
    _check("<figure" not in html[:outer_summary_end] and "<img" not in html[:outer_summary_end], "not in the summary")
    _check(html.startswith('<details class="conoce" open="open"><summary>'), "the panel starts expanded")
    _check(html.index("<details class=\"conoce\"") < html.index("<figure") < html.index('class="conoce-lens"'), "first inside")
    no_photo = _render(_knowledge(profile(), data=auto(taxon(1))))
    _check("<figure" not in no_photo, "no photo, no figure")


def test_panel_open_state_follows_the_constant() -> None:
    knowledge = _photo_knowledge()
    with mock.patch("dashboard.conoce.PANEL_STARTS_OPEN", False):
        closed = _render(knowledge)
    _check(closed.startswith('<details class="conoce"><summary>') and closed.count('open="open"') == 1, "closed: only the first lens is open")
    _check(_render(knowledge).startswith('<details class="conoce" open="open">'), "expanded by default")


def test_figure_markup_and_caption() -> None:
    html = _render(_photo_knowledge())
    image = _opening_tag(html, "img")
    expected_src = f'src="{STATIC_PREFIX}/beaches/{"b" * 64}.webp"'
    for attribute in (expected_src, 'width="480"', 'height="320"', 'loading="lazy"', 'decoding="async"',
                      'alt="Palmeras y arena blanca"'):
        _check(attribute in image, f"figure image missing {attribute}")
    caption = html.split("<figcaption", 1)[1].split("</figcaption>", 1)[0]
    _check('class="conoce-fine"' in caption, "the same style as source lines")
    for part in ("Foto de referencia · 2024 · ", "Autora de prueba", 'title="Foto de"', 'title="Licencia"', ">CC BY-SA</a>", ">Fuente</a>"):
        _check(part in caption, f"caption missing {part}")
    _check("/licenses/by-sa/3.0/" in caption, "links the photo's own licence version, not the latest deed")
    _check("commons.wikimedia.org/wiki/File:Prueba.jpg" in caption, "links the source")
    english = render_conoce_html(_photo_knowledge(), "en", LABELS | {"reference_photo": "Reference photo"})
    _check("Reference photo" in english and 'alt="Palm trees and white sand"' in english, "English caption and alt")


def test_caption_without_a_year() -> None:
    html = _render(_photo_knowledge(None))
    _check("Foto de referencia · <span" in html, "no year, and no empty separator")


def test_photo_alone_shows_the_panel() -> None:
    bare = _knowledge(profile()) | {"photo": beach_photo(), "species": [], "roca": None, "historia": []}
    html = _render(bare)
    _check("<figure" in html and "conoce-lens" not in html, "a photo alone is content")
    _check(_render(bare | {"photo": None}) == "", "and without it nothing shows")
    with mock.patch.object(cd, "knowledge_for", return_value=bare):
        _check(cd.has_content(BEACH)["photo"] is True, "has_content counts the photo")
        _check(not any(v for k, v in cd.has_content(BEACH).items() if k != "photo"), "the lenses stay empty")
    with mock.patch.object(cd, "knowledge_for", return_value=None):
        _check(not any(cd.has_content(BEACH).values()), "no knowledge, no content")


def test_every_src_is_local_and_hashed() -> None:
    html = _render(_photo_knowledge())
    sources = re.findall(r'<img[^>]* src="([^"]*)"', html)
    _check(len(sources) == 2 and all(not src.startswith("http") for src in sources), f"sources: {sources}")
    _check(all(re.fullmatch(rf"{STATIC_PREFIX}/(beaches|species)/[0-9a-f]{{64}}\.webp", src) for src in sources), f"{sources}")
    _check(STATIC_PREFIX == "/app/static", "Streamlit's path, absolute so it works on /beach/<name> URLs")
    _check("inaturalist-open-data" not in html, "the remote photo URL isn't rendered")


def test_real_panels_load_no_remote_image() -> None:
    for beach in BEACHES:
        knowledge = cd.knowledge_for(beach["name"])
        for lang in cd.LANGUAGES:
            html = render_conoce_html(knowledge, lang, LABELS)
            for src in re.findall(r'<img[^>]* src="([^"]*)"', html):
                local = re.fullmatch(rf"{STATIC_PREFIX}/(species|beaches)/[0-9a-f]{{64}}\.webp", src)
                _check(local is not None, f"{beach['name']}: {src}")
                _check((cd.STATIC_DIR / src.removeprefix(f"{STATIC_PREFIX}/")).is_file(), f"missing file: {src}")


def test_bad_photo_files_are_never_rendered() -> None:
    bad_files = [
        "https://evil.test/x.webp",
        "beaches/../../secret.webp",
        "beaches/" + "b" * 63 + ".webp",
        "species/" + "b" * 64 + ".webp",
        'beaches/x"onerror="alert(1).webp',
        None,
    ]
    for file in bad_files:
        html = _render(_photo_knowledge(file=file))
        _check("<figure" not in html and "evil.test" not in html and "secret" not in html, f"rendered {file}")
    species_file = taxon(1)
    species_file["photo"]["file"] = "beaches/" + "a" * 64 + ".webp"
    html = _render(_knowledge(profile(), data=auto(taxon(1))) | {"species": [
        dict(_knowledge(profile(), data=auto(taxon(1)))["species"][0], photo=species_file["photo"])
    ]})
    _check("<img" not in html and "conoce-icon" in html, "a species can't point at another folder")


def test_alt_is_escaped_and_never_empty() -> None:
    html = _render(_photo_knowledge(alt=loc('Sol "y" <b>mar</b>', "x")))
    _check('alt="Sol &quot;y&quot; &lt;b&gt;mar&lt;/b&gt;"' in html, "alt is encoded")
    for empty in ("", "   "):
        _check("<figure" not in _render(_photo_knowledge(alt=loc(empty, empty))), "no alt, no photo")


def test_unsafe_credit_links_hide_the_photo() -> None:
    for field in ("license_url", "source_url"):
        html = _render(_photo_knowledge(**{field: "javascript:alert(1)"}))
        _check("<figure" not in html and "javascript:" not in html, f"unsafe {field}")
    _check("<figure" not in _render(_photo_knowledge(license="cc-by-nc")), "a licence we don't allow")


def test_budgets() -> None:
    knowledge = _full_knowledge() | {"photo": beach_photo() | {"author": "a" * cd.MAX_ATTRIBUTION_CHARS,
                                                              "alt": loc("a" * cd.MAX_ALT_CHARS, "a" * cd.MAX_ALT_CHARS)}}
    sizes = {lang: len(_render(knowledge, lang).encode("utf-8")) for lang in cd.LANGUAGES}
    for lang, size in sizes.items():
        _check(size <= _MAX_PANEL_BYTES, f"{lang}: full panel is {size} bytes")
    runs = timeit.repeat(lambda: _render(knowledge), repeat=_RENDER_RUNS, number=1)
    median_ms = statistics.median(runs) * 1000
    _check(median_ms <= _MAX_RENDER_MS, f"render median {median_ms:.2f} ms")
    print(f"  full panel: {sizes['es']} bytes; render median over {_RENDER_RUNS} runs: {median_ms:.3f} ms")


def test_does_no_io() -> None:
    knowledge = _full_knowledge()
    blocked = AssertionError("the render path must do no I/O")
    with mock.patch.object(builtins, "open", side_effect=blocked), \
            mock.patch.object(socket, "socket", side_effect=blocked):
        _render(knowledge)


def test_style_tag_stays_out_of_the_card() -> None:
    # beaches.py hides any container holding <style>, so one inside the panel
    # would hide the whole beach card, including the risk reading.
    _check("<style" not in _render(_full_knowledge()), "style tag in the card")
    with mock.patch("dashboard.conoce._cached_panel", return_value="<details></details>"), \
            mock.patch("dashboard.conoce.st.markdown") as markdown:
        panel = conoce_panel("Playa", "es", LABELS)
    _check("<style" not in panel, "style tag returned to the card")
    _check("<style>" in markdown.call_args.args[0], "styles must be injected separately")


def test_panel_failure_is_contained() -> None:
    _check(conoce_panel("Playa que no existe", "es", LABELS) == "", "unknown beach shows nothing")
    with mock.patch("dashboard.conoce._cached_panel", side_effect=KeyError("boom")):
        _check(conoce_panel("Playa", "es", LABELS) == "", "a failure must not reach the card")


def _beaches_tree() -> ast.Module:
    return ast.parse(_BEACHES_PY.read_text(encoding="utf-8"))


def test_labels_exist_in_both_languages() -> None:
    for node in _beaches_tree().body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "_T":
            table = ast.literal_eval(node.value)
            break
    else:
        raise AssertionError("_T not found in beaches.py")
    for lang in cd.LANGUAGES:
        for key in LABEL_KEYS:
            _check(table[lang].get(key, "").strip() != "", f"_T[{lang}] is missing {key}")
    _check(table["es"]["see_more"].format(n=1) != table["es"]["see_more"], "see_more needs {n}")


def test_architecture_boundaries() -> None:
    calls = [
        node for node in ast.walk(_beaches_tree())
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "conoce_panel"
    ]
    _check(len(calls) == 1, "beaches.py should call conoce_panel exactly once")
    source = _BEACHES_PY.read_text(encoding="utf-8")
    bubble = source[source.index("_bubble = ("):]
    _check(
        bubble.index("open_maps") < bubble.index("conoce_panel(") < bubble.index("more_details"),
        "the panel belongs between the Maps button and Más detalles",
    )
    render = (_REPO / "dashboard" / "conoce.py").read_text(encoding="utf-8")
    for forbidden in ("import httpx", "import requests", "import socket", "urllib.request"):
        _check(forbidden not in render, f"the render layer must not use {forbidden}")
    for name in ("run", "dispatch"):
        pipeline = (_REPO / "pipeline" / f"{name}.py").read_text(encoding="utf-8")
        _check("conoce" not in pipeline, f"pipeline/{name}.py must not touch this feature")


def main() -> int:
    logging.disable(logging.CRITICAL)
    tests = [(name, fn) for name, fn in globals().items() if name.startswith("test_") and callable(fn)]
    for name, test in tests:
        test()
        print(f"  ok {name}")
    print(f"All {len(tests)} conoce tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
