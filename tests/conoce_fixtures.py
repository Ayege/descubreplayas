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

"""Shared fake data for the "Conoce esta playa" tests. Never shipped."""
from __future__ import annotations

BEACH = "Playa Bávaro"
UNIT = "unidad_prueba"
SOURCE = {"label": "Fuente de prueba", "url": "https://example.org/fuente"}
IMAGE = "https://inaturalist-open-data.s3.amazonaws.com/photos/123/square.jpg"
XSS = "<script>alert(1)</script>\"'"
LABELS = {
    "ecosystem": "Ecosistema",
    "wildlife": "Fauna",
    "conoce_title": "Conoce",
    "lens_vida": "Vida",
    "lens_roca": "Roca",
    "lens_historia": "Historia",
    "see_more": "Ver más ({n})",
    "source": "Fuente",
    "sources": "Fuentes",
    "photos_credit": "Foto de la especie",
    "sand": "Arena",
    "sargassum_why": "Por qué llega",
    "sargassum_note": "Sargazo",
}


def loc(es: str = "texto", en: str = "text") -> dict:
    return {"es": es, "en": en}


def sourced(**fields) -> dict:
    return {"method": "curated", "sources": [dict(SOURCE)], **fields}


def photo() -> dict:
    return {
        "url": IMAGE,
        "license_code": "cc-by",
        "attribution": "Persona de prueba",
        "source_url": "https://www.inaturalist.org/photos/123",
        "origin": "taxon_photos",
        "file": "species/" + "a" * 64 + ".webp",
        "width": 96,
        "height": 96,
        "sha256": "a" * 64,
    }


def taxon(taxon_id: int = 1, group: str | None = "Aves", marine: bool = False, with_photo: bool = True) -> dict:
    return {
        "taxon_id": taxon_id,
        "scientific_name": f"Testus n{taxon_id}",
        "common_name": {"es": f"Especie {taxon_id}", "en": f"Species {taxon_id}"},
        "iconic_group": group,
        "marine": marine,
        "marine_source": "obis",
        "marine_tagged_at": "2026-11-04",
        "photo": photo() if with_photo else None,
        "method": "api:inaturalist",
        "fetched_at": "2026-11-04",
    }


def auto(*taxa: dict, counts: dict[int, int] | None = None, beach: str = BEACH) -> dict:
    counts = counts or {}
    return {
        "taxa": {str(t["taxon_id"]): t for t in taxa},
        "beaches": {beach: [{"taxon_id": t["taxon_id"], "observation_count": counts.get(t["taxon_id"], 1)} for t in taxa]},
    }


def note(blurb: str = "nota es", sargassum: bool = False) -> dict:
    return sourced(blurb=loc(blurb, "note en"), sargassum_note=loc("sarg es", "sarg en") if sargassum else None)


def vida(pinned: list | None = None, hidden: list | None = None, notes: dict | None = None) -> dict:
    return {"pinned_taxa": pinned or [], "hidden_taxa": hidden or [], "notes": notes or {}}


def roca() -> dict:
    return sourced(title=loc("título es", "title en"), story=loc("roca es", "rock en"), rock_type="caliza", sand=loc("arena es", "sand en"))


def historia(title: str = "hecho", year: int | None = 1900) -> dict:
    return sourced(title=loc(f"{title} es", f"{title} en"), body=loc("cuerpo es", "body en"), year=year)


def teaser() -> dict:
    return sourced(text=loc("avance es", "teaser en"))


def unit(with_roca: bool = True, why: bool = False) -> dict:
    return {
        "name": loc("Unidad", "Unit"),
        "roca": roca() if with_roca else None,
        "sargazo_por_que": loc("por qué es", "why en") if why else None,
    }


def profile(**fields) -> dict:
    return {"coastal_unit": UNIT, "teaser": None, "vida": vida(), "historia": [], **fields}


def full_inputs() -> tuple[dict, dict, dict]:
    """Profiles, units and species with every budget filled, for size and speed."""
    taxa = [taxon(i, group=f"Grupo{i}", with_photo=i <= 3) for i in range(1, 9)]
    notes = {t["taxon_id"]: note("n" * 140, sargassum=True) for t in taxa}
    stuffed_roca = roca() | {"story": loc("r" * 400, "s" * 400), "sand": loc("a" * 140, "a" * 140)}
    units = {UNIT: {"name": loc(), "roca": stuffed_roca, "sargazo_por_que": loc("w" * 200, "w" * 200)}}
    items = [historia(f"hecho {i}") | {"body": loc("c" * 500, "b" * 500)} for i in range(3)]
    stuffed = teaser() | {"text": loc("a" * 90, "t" * 90)}
    profiles = {BEACH: profile(teaser=stuffed, vida=vida(notes=notes), historia=items)}
    return profiles, units, auto(*taxa)
