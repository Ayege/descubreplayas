# Copyright 2026 Ayesha Yege
#
# This file is DATA, not code. It is licensed under the Creative Commons
# Attribution 4.0 International License (CC BY 4.0) -- NOT the Apache License
# that covers the rest of this repository.
#
#     https://creativecommons.org/licenses/by/4.0/
#
# Share and adapt it freely, including commercially, provided you give
# appropriate credit. See LICENSE-DATA.md for the attribution line to use.

"""Hand-written "Conoce esta playa" content.

The shapes are the TypedDicts in dashboard/conoce_data.py. Every fact carries a
method and at least one source URL, and tests/test_profiles.py rejects anything
that does not. Leave a slot empty rather than guess at it: the UI hides it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dashboard.conoce_data import BeachProfile, CoastalUnit

# Corrections to the marine tag from OBIS, by iNaturalist taxon id. For ranking,
# "marine" means it lives in the sea or the intertidal zone; a coastal tree whose
# seeds float does not count. OBIS lists some land species as marine because they
# grow on the coast. hidden_taxa is for "never show"; this is for fixing a tag.
# Check each id against the API's returned name before adding it, never from memory.
MARINE_OVERRIDES: dict[int, bool] = {
    # Cocos nucifera: OBIS sets marine, brackish, terrestrial and freshwater.
    48865: False,
    # Barringtonia asiatica: a tree of beaches and sandy coasts.
    # https://powo.science.kew.org/taxon/urn:lsid:ipni.org:names:469061-1
    # https://powo.science.kew.org/taxon/urn:lsid:ipni.org:names:329914-2/general-information
    189657: False,
    # Hernandia sonora: a tree of the wet tropical biome.
    # https://powo.science.kew.org/taxon/urn:lsid:ipni.org:names:316357-2
    278899: False,
    # Cassytha filiformis: a parasitic vine scrambling over shoreline bushes and trees.
    # https://powo.science.kew.org/taxon/urn:lsid:ipni.org:names:463202-1
    # https://www.nparks.gov.sg/florafaunaweb/flora/1/3/1363
    160172: False,
    # Rhinella marina: a land and freshwater toad, despite its name. OBIS sets brackish.
    # https://iucngisd.org/gisd/pdf.php?sc=113
    # https://animaldiversity.org/accounts/bufo_marinus
    553515: False,
}

# PROVISIONAL grouping, based on geography only, not on verified geology. A unit
# is nothing more than a key that beaches point to, so Phase C4 can split one by
# changing that key. Known questions for C4:
# - samana_sur_y_este mixes Cayo Levantado (inside sheltered Samaná Bay) and
#   El Valle (open north coast). That is fine for rock, but one sargazo_por_que
#   line cannot be true for both: split the unit or leave that line empty.
# - costa_ambar runs from Cabarete to Cambiaso and on past Puerto Plata. It is the
#   first candidate for a split at Puerto Plata.
# - Playa Maimón is in costa_ambar because its province and description place it
#   near Amber Cove. Its coordinates in beaches_data.py point near Río San Juan
#   instead, about 70 km east, and need checking.
COASTAL_UNITS: dict[str, CoastalUnit] = {
    "costa_del_coco": {
        "name": {"es": "Costa del Coco", "en": "Coconut Coast"},
        "roca": None,
        "sargazo_por_que": None,
    },
    "bayahibe_saona": {
        "name": {"es": "Bayahibe y Saona", "en": "Bayahibe and Saona"},
        "roca": None,
        "sargazo_por_que": None,
    },
    "caribe_central": {
        "name": {"es": "Caribe central", "en": "Central Caribbean"},
        "roca": None,
        "sargazo_por_que": None,
    },
    "san_cristobal_azua": {
        "name": {"es": "San Cristóbal y Azua", "en": "San Cristóbal and Azua"},
        "roca": None,
        "sargazo_por_que": None,
    },
    "barahona": {
        "name": {"es": "Barahona", "en": "Barahona"},
        "roca": None,
        "sargazo_por_que": None,
    },
    "pedernales": {
        "name": {"es": "Pedernales", "en": "Pedernales"},
        "roca": None,
        "sargazo_por_que": None,
    },
    "samana_sur_y_este": {
        "name": {"es": "Samaná sur y este", "en": "South and east Samaná"},
        "roca": None,
        "sargazo_por_que": None,
    },
    "las_terrenas": {
        "name": {"es": "Las Terrenas", "en": "Las Terrenas"},
        "roca": None,
        "sargazo_por_que": None,
    },
    "rio_san_juan": {
        "name": {"es": "Río San Juan", "en": "Río San Juan"},
        "roca": None,
        "sargazo_por_que": None,
    },
    "costa_ambar": {
        "name": {"es": "Costa de Ámbar", "en": "Amber Coast"},
        "roca": None,
        "sargazo_por_que": None,
    },
    "noroeste": {
        "name": {"es": "Noroeste", "en": "Northwest"},
        "roca": None,
        "sargazo_por_que": None,
    },
}

# Keyed by the exact beach name in BEACHES.
PROFILES: dict[str, BeachProfile] = {
    "Playa Bávaro": {
        "coastal_unit": "costa_del_coco",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Macao": {
        "coastal_unit": "costa_del_coco",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Juanillo": {
        "coastal_unit": "costa_del_coco",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Blanca": {
        "coastal_unit": "costa_del_coco",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Bayahibe Beach": {
        "coastal_unit": "bayahibe_saona",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Dominicus Beach": {
        "coastal_unit": "bayahibe_saona",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Canto de la Playa (Saona)": {
        "coastal_unit": "bayahibe_saona",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Dorada": {
        "coastal_unit": "costa_ambar",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Sosúa": {
        "coastal_unit": "costa_ambar",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Encuentro": {
        "coastal_unit": "costa_ambar",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Cabarete Beach": {
        "coastal_unit": "costa_ambar",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Punta Rucia": {
        "coastal_unit": "noroeste",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "La Ensenada": {
        "coastal_unit": "noroeste",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Grande": {
        "coastal_unit": "rio_san_juan",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Caletón": {
        "coastal_unit": "rio_san_juan",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Rincón": {
        "coastal_unit": "samana_sur_y_este",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Frontón": {
        "coastal_unit": "samana_sur_y_este",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Madama": {
        "coastal_unit": "samana_sur_y_este",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Bonita": {
        "coastal_unit": "las_terrenas",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Cosón": {
        "coastal_unit": "las_terrenas",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Las Ballenas": {
        "coastal_unit": "las_terrenas",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Cayo Levantado": {
        "coastal_unit": "samana_sur_y_este",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Boca Chica Beach": {
        "coastal_unit": "caribe_central",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Juan Dolio": {
        "coastal_unit": "caribe_central",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Guayacanes": {
        "coastal_unit": "caribe_central",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Bahía de las Águilas": {
        "coastal_unit": "pedernales",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Cabo Rojo": {
        "coastal_unit": "pedernales",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Blanca (Pedernales)": {
        "coastal_unit": "pedernales",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa El Cortecito": {
        "coastal_unit": "costa_del_coco",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Cabeza de Toro": {
        "coastal_unit": "costa_del_coco",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Uvero Alto": {
        "coastal_unit": "costa_del_coco",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Bibijagua": {
        "coastal_unit": "costa_del_coco",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Minitas": {
        "coastal_unit": "bayahibe_saona",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Isla Catalina Beach": {
        "coastal_unit": "bayahibe_saona",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Mano Juan (Saona)": {
        "coastal_unit": "bayahibe_saona",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Cofresí": {
        "coastal_unit": "costa_ambar",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Costambar": {
        "coastal_unit": "costa_ambar",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Alicia": {
        "coastal_unit": "costa_ambar",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Cambiaso": {
        "coastal_unit": "costa_ambar",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Maimón": {
        "coastal_unit": "costa_ambar",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Kite Beach": {
        "coastal_unit": "costa_ambar",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Sosúa Bay (Charamicos)": {
        "coastal_unit": "costa_ambar",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Diamante": {
        "coastal_unit": "rio_san_juan",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa El Bretón": {
        "coastal_unit": "rio_san_juan",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Las Galeras": {
        "coastal_unit": "samana_sur_y_este",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa El Valle": {
        "coastal_unit": "samana_sur_y_este",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Punta Popy": {
        "coastal_unit": "las_terrenas",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Najayo": {
        "coastal_unit": "san_cristobal_azua",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Palenque": {
        "coastal_unit": "san_cristobal_azua",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Palmar de Ocoa": {
        "coastal_unit": "san_cristobal_azua",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Boca de Yuma": {
        "coastal_unit": "bayahibe_saona",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa San Rafael": {
        "coastal_unit": "barahona",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Los Patos": {
        "coastal_unit": "barahona",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Quemaíto": {
        "coastal_unit": "barahona",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Saladilla": {
        "coastal_unit": "barahona",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
    "Playa Juan de Bolaños": {
        "coastal_unit": "noroeste",
        "teaser": None,
        "vida": {"pinned_taxa": [], "hidden_taxa": [], "notes": {}},
        "historia": [],
    },
}
