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

"""Spanish translations for the beach dataset.

`beaches_data.py` is the canonical dataset and stays in English: it is what the
API, the Supabase seeder, and the filter logic all key on. This module is a
pure display layer on top of it — nothing here is ever used for matching or
storage, only for what the reader sees.

Two lookups:

* ``TERMS_ES``      — closed vocabularies (regions, activities, wildlife,
                      facilities, access types, fees, seasons). Values repeat
                      across beaches, so one entry serves many.
* ``BEACH_TEXT_ES`` — per-beach free text (description, access notes,
                      ecosystem, water conditions), keyed by beach name.

Both are looked up with ``.get(value, value)``, so an English value with no
translation falls through unchanged rather than blowing up. That keeps the app
working when a beach is added to the dataset before its Spanish copy exists.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------
TERMS_ES: dict[str, str] = {
    # ── Regions ────────────────────────────────────────────────────────────
    "East (Punta Cana / La Romana)": "Este (Punta Cana / La Romana)",
    "North (Puerto Plata / Cabarete)": "Norte (Puerto Plata / Cabarete)",
    "Samaná Peninsula": "Península de Samaná",
    "South (Santo Domingo / South Coast)": "Sur (Santo Domingo / Costa Sur)",
    "Southwest (Barahona / Pedernales)": "Suroeste (Barahona / Pedernales)",

    # ── Access type ────────────────────────────────────────────────────────
    "4x4 or Boat": "4x4 o bote",
    "Boat": "Bote",
    "Boat or 4x4": "Bote o 4x4",
    "Boat or Hiking Trail": "Bote o sendero a pie",
    "Controlled": "Acceso controlado",
    "Public": "Público",
    "Road": "Por carretera",
    "Road or Boat": "Carretera o bote",

    # ── Beach type ─────────────────────────────────────────────────────────
    "Diving": "Buceo",
    "Eco": "Ecológica",
    "Family": "Familiar",
    "Fishing": "Pesca",
    "Island": "Isla",
    "Local": "Local",
    "Long": "Extensa",
    "Quiet": "Tranquila",
    "Remote": "Remota",
    "Resort": "De resort",
    "Scenic": "Panorámica",
    "Snorkeling": "Snorkel",
    "Surfing": "Surf",
    "Town": "De pueblo",
    "Upscale": "Exclusiva",
    "Urban": "Urbana",
    "Wild": "Salvaje",
    "Wind Sports": "Deportes de viento",

    # ── Entrance fee ───────────────────────────────────────────────────────
    "Dock fee (often included in tours)":
        "Tarifa de muelle (suele venir incluida en los tours)",
    "Free": "Gratis",
    "Free (boat extra)": "Gratis (el bote se paga aparte)",
    "Free (paid parking)": "Gratis (parqueo de pago)",
    "Free (resort area)": "Gratis (zona de resorts)",
    "Free (tours extra)": "Gratis (los tours se pagan aparte)",
    "Included in tours": "Incluida en los tours",
    "Park permit via tour operators":
        "Permiso del parque a través de tour operadores",
    "Resort access": "Acceso por el resort",
    "Small park fee": "Cuota pequeña del parque",
    "Varies": "Variable",

    # ── Best time to visit ─────────────────────────────────────────────────
    "April–September (clearest water)": "Abril–septiembre (agua más clara)",
    "December–April (avoid Jun–Nov storms)":
        "Diciembre–abril (evita las tormentas de jun–nov)",
    "December–April (calm seas)": "Diciembre–abril (mar en calma)",
    "December–April (dry season)": "Diciembre–abril (temporada seca)",
    "December–April (dry, calm season)":
        "Diciembre–abril (temporada seca y tranquila)",
    "January–March (whale season)": "Enero–marzo (temporada de ballenas)",
    "June–August & December–February (peak wind)":
        "Junio–agosto y diciembre–febrero (viento más fuerte)",
    "June–August (peak wind)": "Junio–agosto (viento más fuerte)",
    "November–March (dry season)": "Noviembre–marzo (temporada seca)",
    "October–April (best surf)": "Octubre–abril (mejor surf)",
    "October–April (consistent swell)": "Octubre–abril (oleaje constante)",

    # ── Activities ─────────────────────────────────────────────────────────
    "Beach Clubs": "Clubes de playa",
    "Beach Restaurants": "Restaurantes en la playa",
    "Beach Volleyball": "Voleibol de playa",
    "Birdwatching": "Observación de aves",
    "Boat Excursions": "Excursiones en bote",
    "Boat Tours": "Paseos en bote",
    "Boating": "Navegación",
    "Catamaran Tours": "Tours en catamarán",
    "Cliff Photography": "Fotografía de acantilados",
    "Ecotourism": "Ecoturismo",
    "Exploring Caves": "Exploración de cuevas",
    "Family Recreation": "Recreación familiar",
    "Foiling": "Foiling",
    "Freediving": "Apnea",
    "Golf": "Golf",
    "Hiking": "Senderismo",
    "Horseback Riding": "Paseos a caballo",
    "Jet Ski": "Jet ski",
    "Jogging": "Trotar",
    "Kayaking": "Kayak",
    "Kiteboarding": "Kiteboarding",
    "Kitesurfing": "Kitesurf",
    "Paddleboarding": "Paddleboard",
    "Parasailing": "Parasailing",
    "Photography": "Fotografía",
    "Relaxation": "Descanso",
    "Restaurants": "Restaurantes",
    "Scuba Diving": "Buceo con tanque",
    "Surf Lessons": "Clases de surf",
    "Swimming": "Natación",
    "Water Sports": "Deportes acuáticos",
    "Whale Watching": "Avistamiento de ballenas",
    "Windsurfing": "Windsurf",
    # NOTE: the vocabularies share one namespace on purpose, so a term reads
    # the same wherever it appears. Values that belong to more than one field
    # are therefore listed exactly once: "Diving", "Fishing", "Snorkeling" and
    # "Surfing" sit in the beach-type block above, "Swimming" here, and
    # "Beach Clubs" / "Boat Tours" / "Restaurants" / "Water Sports" here rather
    # than repeated under facilities.

    # ── Wildlife ───────────────────────────────────────────────────────────
    "American Flamingo": "Flamenco americano",
    "Angelfish": "Peces ángel",
    "Brown Pelican": "Pelícano pardo",
    "Brown Pelicans": "Pelícanos pardos",
    "Coastal Birds": "Aves costeras",
    "Coral Fish": "Peces de coral",
    "Coral Species": "Especies de coral",
    "Crabs": "Cangrejos",
    "Fish": "Peces",
    "Flamingos": "Flamencos",
    "Frigatebirds": "Tijeretas",
    "Green Sea Turtle": "Tortuga verde",
    "Hawksbill Sea Turtle": "Tortuga carey",
    "Hawksbill Turtles": "Tortugas carey",
    "Humpback Whales": "Ballenas jorobadas",
    "Iguanas": "Iguanas",
    "Mangrove Birds": "Aves de manglar",
    "Marine Fish": "Peces marinos",
    "Marine Invertebrates": "Invertebrados marinos",
    "Migratory Birds": "Aves migratorias",
    "Nurse Sharks": "Tiburones gata",
    "Parrotfish": "Peces loro",
    "Pelicans": "Pelícanos",
    "Reef Fish": "Peces de arrecife",
    "Rhinoceros Iguana": "Iguana rinoceronte",
    "Sea Birds": "Aves marinas",
    "Sea Fans": "Abanicos de mar",
    "Sea Stars": "Estrellas de mar",
    "Sea Turtles": "Tortugas marinas",
    "Sea Urchins": "Erizos de mar",
    "Shorebirds": "Aves playeras",
    "Spotted Drumfish": "Pez tambor manchado",
    "Starfish": "Estrellas de mar",
    "Tropical Fish": "Peces tropicales",

    # ── Facilities ─────────────────────────────────────────────────────────
    "A Few Local Eateries": "Algunos comedores locales",
    "Bars": "Bares",
    "Beach Bars": "Bares en la playa",
    "Beach Club": "Club de playa",
    "Beachfront Seafood Restaurants":
        "Restaurantes de mariscos frente al mar",
    "Boardwalk": "Malecón",
    "Boardwalks": "Malecones",
    "Boat Docks": "Muelles",
    "Boat Operators": "Operadores de bote",
    "Boat Trips": "Viajes en bote",
    "Boat Vendors": "Vendedores de paseos en bote",
    "Boutique Hotels": "Hoteles boutique",
    "Cabañas": "Cabañas",
    "Chair Rentals": "Alquiler de sillas",
    "Chairs": "Sillas",
    "Changing Areas": "Área para cambiarse",
    "Changing Rooms": "Vestidores",
    "Dive Centers": "Centros de buceo",
    "Dive Operators": "Operadores de buceo",
    "Dive Shops": "Tiendas de buceo",
    "Food Stands": "Puestos de comida",
    "Hotels": "Hoteles",
    "Kite Schools": "Escuelas de kite",
    "Lifeguards (main beach)": "Salvavidas (playa principal)",
    "Lifeguards (peak season)": "Salvavidas (temporada alta)",
    "Lifeguards (resort sections)": "Salvavidas (zonas de resort)",
    "Limited Local Vendors": "Pocos vendedores locales",
    "Limited Vendors": "Pocos vendedores",
    "Local Food Stands": "Puestos de comida local",
    "Local Food Vendors": "Vendedores de comida local",
    "Local Restaurants": "Restaurantes locales",
    "Local Seafood Restaurants": "Restaurantes locales de mariscos",
    "Natural Lagoon": "Laguna natural",
    "Natural Pools": "Piscinas naturales",
    "Nature Path": "Sendero natural",
    "Nearby Restaurants": "Restaurantes cercanos",
    "None (bring supplies)": "Ninguno (lleva tus provisiones)",
    "None (carry all food, water, fuel)":
        "Ninguno (lleva comida, agua y combustible)",
    "Ocean World nearby": "Ocean World cerca",
    "Outdoor Shower": "Ducha al aire libre",
    "Outhouses": "Letrinas",
    "Palapa Restaurants": "Restaurantes de palapa",
    "Picnic Areas": "Áreas de picnic",
    "Picnic Tables": "Mesas de picnic",
    "Rental Schools": "Escuelas y alquiler de equipos",
    "Rentals": "Alquiler de equipos",
    "Resorts": "Resorts",
    "Restaurant": "Restaurante",
    "Restrooms": "Baños",
    "Seafood Restaurants": "Restaurantes de mariscos",
    "Security": "Seguridad",
    "Shops": "Tiendas",
    "Showers": "Duchas",
    "Small Guesthouses": "Pequeñas hospederías",
    "Small Local Eateries": "Comedores locales pequeños",
    "Snack Stands": "Puestos de picadera",
    "Souvenir Market": "Mercado de souvenirs",
    "Sunlounger Rentals": "Alquiler de camastros",
    "Surf Schools": "Escuelas de surf",
    "Tour Vendors (limited)": "Vendedores de tours (pocos)",
    "Tour Vendors (no permanent facilities)":
        "Vendedores de tours (sin instalaciones fijas)",
    "Turtle Sanctuary": "Santuario de tortugas",
    "Vacation Villas": "Villas de alquiler",
}


# ---------------------------------------------------------------------------
# Per-beach free text
# ---------------------------------------------------------------------------
# Keys map to the beaches_data field names: description, access_description,
# ecosystem, water_conditions.
BEACH_TEXT_ES: dict[str, dict[str, str]] = {
    "Playa Bávaro": {
        "description": "Clásica playa de arena blanca bordeada de palmeras en la Costa del Coco, con todas las comodidades de resort y muchos deportes acuáticos.",
        "access_description": "Puntos de acceso público entre los resorts, saliendo de la carretera principal de Punta Cana.",
        "ecosystem": "Arrecife de coral y costa de cocoteros",
        "water_conditions": "Tranquila y turquesa; corrientes de resaca ocasionales",
    },
    "Playa Macao": {
        "description": "Playa salvaje y sin desarrollar, de arena dorada y famosa por el surf; casi sin servicios formales, así que ven preparado.",
        "access_description": "Camino local asfaltado a ~20–30 min de Bávaro; parqueo pequeño.",
        "ecosystem": "Playa abierta al Atlántico con acantilados de roca coralina",
        "water_conditions": "Oleaje y corrientes fuertes; respeta las banderas de aviso",
    },
    "Playa Juanillo": {
        "description": "Playa exclusiva de arena blanca impecable dentro de Cap Cana, seguido entre las mejores del país.",
        "access_description": "Dentro de Cap Cana (zona cerrada); parqueo público cerca del restaurante Tortuga Bay.",
        "ecosystem": "Costa caribeña protegida",
        "water_conditions": "Generalmente tranquila, con brisa constante hacia la costa",
    },
    "Playa Blanca": {
        "description": "Ensenada en media luna junto a la marina de Cap Cana, con un club de playa popular y agua tranquila para nadar.",
        "access_description": "En la zona de la marina de Cap Cana; se llega en carro o en transporte del resort.",
        "ecosystem": "Ensenada caribeña resguardada",
        "water_conditions": "Tranquila y poco profunda",
    },
    "Bayahibe Beach": {
        "description": "Playa relajada de pueblo pesquero y puerta de entrada para los viajes en bote a las islas Saona y Catalina.",
        "access_description": "Acceso fácil por carretera desde La Romana; parqueo público y cuota pequeña del parque.",
        "ecosystem": "Parque Nacional Cotubanamá (Parque Nacional del Este)",
        "water_conditions": "Clara y tranquila",
    },
    "Dominicus Beach": {
        "description": "Playa desarrollada con certificación Bandera Azul junto a Bayahibe, de arena suave y arrecifes cerca de la orilla.",
        "access_description": "Acceso fácil por carretera cerca de Bayahibe; bordeada de resorts pero con acceso público.",
        "ecosystem": "Parque Nacional Cotubanamá (Bandera Azul)",
        "water_conditions": "Clara, arenosa y tranquila",
    },
    "Canto de la Playa (Saona)": {
        "description": "Playa de arena de talco en la protegida Isla Saona, rodeada de arrecife vivo y piscinas naturales con estrellas de mar.",
        "access_description": "Viaje en bote desde Bayahibe; solo se desembarca en las zonas autorizadas.",
        "ecosystem": "Parque Nacional Cotubanamá (Isla Saona)",
        "water_conditions": "Arena de talco y pozas turquesas poco profundas",
    },
    "Playa Dorada": {
        "description": "Playa de arena dorada de 1.9 km frente al primer complejo de resorts de Puerto Plata: familiar y con ambiente.",
        "access_description": "Pegada a la ciudad de Puerto Plata; parqueo detrás de la plaza Riviera Azul.",
        "ecosystem": "Costa desarrollada de resorts",
        "water_conditions": "Poco profunda y casi siempre tranquila",
    },
    "Playa Sosúa": {
        "description": "Bahía en herradura famosa por el snorkel fácil sobre un arrecife de borde, con un malecón lleno de vida.",
        "access_description": "Por la Carretera 5 cruzando el pueblo de Sosúa; hay parqueo de pago frente a la bahía.",
        "ecosystem": "Bahía en herradura protegida por arrecife de coral",
        "water_conditions": "Poco profunda cerca de la orilla; más honda en el arrecife",
    },
    "Playa Encuentro": {
        "description": "La playa de surf por excelencia del país, con al menos seis rompientes para todos los niveles y varios campamentos de surf.",
        "access_description": "Desvío de la carretera costera ~10 km al oeste de Sosúa; parqueo a la orilla.",
        "ecosystem": "Playa de surf con rompiente de arrecife",
        "water_conditions": "Varias rompientes de arrecife y punta; corrientes de resaca",
    },
    "Cabarete Beach": {
        "description": "La 'Capital de los Deportes Acuáticos', con 250–300 días de viento al año: ideal para kitesurf y windsurf.",
        "access_description": "A ~20 min del aeropuerto de Puerto Plata; parqueo gratis y acceso desde el pueblo.",
        "ecosystem": "Bahía expuesta al viento detrás de un arrecife protector",
        "water_conditions": "Poco profunda dentro del arrecife; vientos alisios constantes",
    },
    "Punta Rucia": {
        "description": "Playa tranquila de pueblo pesquero y punto de salida hacia el banco de arena de postal de Cayo Arena (Paraíso).",
        "access_description": "Camino rural en la costa noroeste; de aquí salen los botes a Cayo Arena.",
        "ecosystem": "Puerta de entrada a Cayo Arena / arrecifes de Montecristi",
        "water_conditions": "Tranquila y clara",
    },
    "La Ensenada": {
        "description": "Playa local y familiar, de agua poco profunda y tranquila — favorita de los dominicanos los fines de semana.",
        "access_description": "Cerca de Punta Rucia en la costa noroeste; acceso por camino local.",
        "ecosystem": "Bahía resguardada y poco profunda",
        "water_conditions": "Muy poco profunda y tranquila",
    },
    "Playa Grande": {
        "description": "Playa larga e imponente de arena dorada, con acantilados y vegetación detrás; oleaje fuerte en los meses de invierno.",
        "access_description": "Saliendo de la carretera costera cerca de Río San Juan; tiene parqueo.",
        "ecosystem": "Ecosistema costero atlántico con acantilados",
        "water_conditions": "Atlántico abierto con oleaje; cuidado con las corrientes",
    },
    "Playa Caletón": {
        "description": "Pequeñísima ensenada turquesa, la 'Playita Caletón', querida por los locales para hacer snorkel y compartir el fin de semana.",
        "access_description": "Ensenada pequeña cerca de Playa Grande y Río San Juan; se entra caminando un tramo corto.",
        "ecosystem": "Ensenada de bolsillo protegida",
        "water_conditions": "Tranquila, clara y poco profunda",
    },
    "Playa Rincón": {
        "description": "Unos 5 km de paraíso sin desarrollar, de arena blanca respaldada por palmeras; siempre entre las mejores playas del mundo.",
        "access_description": "15 min por camino de tierra desde Las Galeras, o un corto viaje en bote local.",
        "ecosystem": "Bosque de palmeras y estuario de río cerca de Los Haitises",
        "water_conditions": "Bahía insólitamente tranquila, resguardada por arrecife",
    },
    "Playa Frontón": {
        "description": "Playa remota al pie de acantilados, con posiblemente el mejor arrecife para snorkel de la península de Samaná.",
        "access_description": "En bote desde Las Galeras, o una caminata exigente por la selva.",
        "ecosystem": "Arrecife de coral bajo acantilados de piedra caliza (zona de Los Haitises)",
        "water_conditions": "Clara, con el mejor snorkel desde la orilla del país",
    },
    "Playa Madama": {
        "description": "Ensenada pequeña y apartada, flanqueada por acantilados imponentes y cuevas marinas; se llega a pie o en bote.",
        "access_description": "Caminata corta o bote desde Las Galeras.",
        "ecosystem": "Ensenada resguardada entre acantilados y cuevas",
        "water_conditions": "Tranquila y resguardada",
    },
    "Playa Bonita": {
        "description": "Playa relajada bordeada de palmeras cerca de Las Terrenas, con hoteles boutique y oleaje suave.",
        "access_description": "Camino asfaltado desde Las Terrenas; parqueo a la orilla y en los hoteles.",
        "ecosystem": "Playa atlántica abierta bordeada de palmeras",
        "water_conditions": "Oleaje moderado; buena para principiantes",
    },
    "Playa Cosón": {
        "description": "Extensión larga y ancha de arena dorada, famosa por sus comedores de mariscos al atardecer y por el espacio para caminar.",
        "access_description": "Camino costero al oeste de Las Terrenas; parqueo abierto de sobra.",
        "ecosystem": "Playa atlántica amplia y abierta",
        "water_conditions": "Agua abierta con olas suaves",
    },
    "Playa Las Ballenas": {
        "description": "Playa del pueblo de Las Terrenas, bautizada por los islotes frente a la costa; agua tranquila y alquiler de equipos a mano.",
        "access_description": "Se llega caminando desde el centro de Las Terrenas.",
        "ecosystem": "Playa urbana frente a tres islotes 'ballena'",
        "water_conditions": "Tranquila y poco profunda",
    },
    "Cayo Levantado": {
        "description": "La 'Isla Bacardí', bordeada de palmeras, con arena de postal, arrecifes para snorkel y tours de ballenas en invierno.",
        "access_description": "En bote desde el pueblo de Samaná o los muelles de los hoteles; la tarifa de muelle suele venir incluida.",
        "ecosystem": "Isla rodeada de arrecife en la bahía de Samaná",
        "water_conditions": "Arena suave y agua clara y poco profunda",
    },
    "Boca Chica Beach": {
        "description": "La playa urbana clásica de la capital: un arrecife rompeolas forma una laguna poco profunda, perfecta para familias.",
        "access_description": "A ~30 km al este de Santo Domingo por la Autopista DR-3; hay guaguas y carros públicos.",
        "ecosystem": "Laguna protegida por arrecife con parches de manglar",
        "water_conditions": "Laguna muy tranquila y poco profunda",
    },
    "Playa Juan Dolio": {
        "description": "Playa amplia bordeada de resorts, cerca de puntos de buceo; excursión cómoda de un día desde Santo Domingo.",
        "access_description": "Carretera costera al este de Boca Chica; en motoconcho o carro.",
        "ecosystem": "Costa arenosa cerca del arrecife de Catalina",
        "water_conditions": "Poco profunda; algunos tramos estrechados por la erosión",
    },
    "Playa Guayacanes": {
        "description": "Playa local sin pretensiones junto a Juan Dolio, con botes de pesca y comedores auténticos de mariscos.",
        "access_description": "Camino costero al lado de Juan Dolio; parqueo local.",
        "ecosystem": "Costa arenosa natural",
        "water_conditions": "Poco profunda; algunos parches rocosos (usa zapatos de agua)",
    },
    "Bahía de las Águilas": {
        "description": "La playa más virgen del país: ~7 km de arena blanca intacta dentro de un parque estrictamente protegido.",
        "access_description": "Pedernales → Cabo Rojo/La Cueva, y los últimos 6–8 km en bote o en 4x4 por camino difícil.",
        "ecosystem": "Parque Nacional Jaragua (Reserva de Biosfera UNESCO)",
        "water_conditions": "Excepcionalmente clara sobre arrecifes sanos",
    },
    "Cabo Rojo": {
        "description": "Bahía tranquila de arena rojiza y principal punto de salida de los botes hacia Bahía de las Águilas.",
        "access_description": "Camino asfaltado/industrial al sur de Pedernales; punto de partida para Bahía de las Águilas.",
        "ecosystem": "Costa del Parque Nacional Jaragua",
        "water_conditions": "Tranquila y clara",
    },
    "Playa Blanca (Pedernales)": {
        "description": "Playa aislada de arena blanca en el extremo suroeste del país, para desconectarse de verdad.",
        "access_description": "Costa remota del extremo suroeste cerca de la frontera con Haití; se recomienda guía.",
        "ecosystem": "Costa árida y remota",
        "water_conditions": "Tranquila y clara",
    },
    "Playa El Cortecito": {
        "description": "Playa animada de pueblo pesquero en Bávaro, con acceso público, comedores de mariscos y un mercado local.",
        "access_description": "Acceso público por el pueblo pesquero, en pleno corazón de Bávaro.",
        "ecosystem": "Costa de arrecife de la Costa del Coco",
        "water_conditions": "Tranquila y poco profunda",
    },
    "Playa Cabeza de Toro": {
        "description": "Playa más tranquila y menos concurrida entre las grandes zonas de resorts, popular para el kitesurf.",
        "access_description": "Tramo tranquilo entre Punta Cana y Bávaro; acceso por camino local.",
        "ecosystem": "Costa bordeada de arrecife con lagunas cercanas",
        "water_conditions": "Tranquila con brisa constante",
    },
    "Playa Uvero Alto": {
        "description": "Playa larga y panorámica en un enclave de resorts más tranquilo al norte, con un aire más salvaje que Bávaro.",
        "access_description": "Al norte de Macao, a ~45 min de Punta Cana; acceso por el camino de los resorts.",
        "ecosystem": "Playa atlántica amplia respaldada por cocotales",
        "water_conditions": "Oleaje y brisa moderados",
    },
    "Playa Bibijagua": {
        "description": "Playa pública favorita de los locales cerca de Punta Cana, animada los fines de semana con familias dominicanas.",
        "access_description": "Playa pública cerca del poblado de Punta Cana, muy visitada por los locales.",
        "ecosystem": "Costa de arena blanca protegida por arrecife",
        "water_conditions": "Tranquila y poco profunda",
    },
    "Playa Minitas": {
        "description": "Playa en media luna, bien cuidada, dentro de Casa de Campo, con agua tranquila y servicio completo de resort.",
        "access_description": "Dentro del resort Casa de Campo; puede que necesites un pase de día.",
        "ecosystem": "Ensenada artificial resguardada",
        "water_conditions": "Muy tranquila y protegida",
    },
    "Isla Catalina Beach": {
        "description": "Isla-arrecife protegida frente a La Romana, célebre por el punto de buceo 'La Pared' y su arena de talco.",
        "access_description": "En bote desde La Romana / Bayahibe; excursiones de cruceros y de buceo.",
        "ecosystem": "Isla-arrecife del Parque Nacional Cotubanamá",
        "water_conditions": "Cristalina sobre paredes de coral",
    },
    "Playa Mano Juan (Saona)": {
        "description": "El colorido caserío pesquero de Isla Saona, con un proyecto comunitario de conservación de tortugas marinas.",
        "access_description": "En bote hasta el único poblado de Saona; forma parte de los tours del parque nacional.",
        "ecosystem": "Pueblo pesquero del Parque Nacional Cotubanamá",
        "water_conditions": "Tranquila, poco profunda y turquesa",
    },
    "Playa Cofresí": {
        "description": "Playa de ensenada junto al parque Ocean World Adventure Park, con buen baño y vistas al atardecer.",
        "access_description": "Al oeste de la ciudad de Puerto Plata; acceso por carretera y entrada pública.",
        "ecosystem": "Ensenada bordeada de arrecife",
        "water_conditions": "Tranquila, con algo de oleaje en los extremos",
    },
    "Playa Costambar": {
        "description": "Playa tranquila, favorita de los extranjeros residentes, de agua suave y ambiente de pueblo relajado.",
        "access_description": "Comunidad residencial de playa justo al oeste de Puerto Plata.",
        "ecosystem": "Costa tranquila y poco profunda",
        "water_conditions": "Tranquila y poco profunda",
    },
    "Playa Alicia": {
        "description": "Alternativa más tranquila de arena blanca a la bahía principal de Sosúa; se llega por un malecón panorámico sobre el acantilado.",
        "access_description": "En Sosúa; se baja por la escalera del malecón del acantilado.",
        "ecosystem": "Ensenada de arena blanca al pie de un acantilado",
        "water_conditions": "Tranquila y clara",
    },
    "Playa Cambiaso": {
        "description": "Playa apartada rodeada de manglares, a la que se llega en 4x4 o en bote; ideal para los amantes de la naturaleza.",
        "access_description": "Playa remota al este de Puerto Plata; camino difícil o bote.",
        "ecosystem": "Manglares y dunas en la desembocadura del río",
        "water_conditions": "Tranquila donde el río se encuentra con el mar",
    },
    "Playa Maimón": {
        "description": "Playa de bahía local cerca de Amber Cove, con arrecifes accesibles muy usados para el buceo de principiantes.",
        "access_description": "Cerca del poblado de Maimón, al oeste del puerto de cruceros Amber Cove.",
        "ecosystem": "Bahía con arrecife de coral frente a la costa",
        "water_conditions": "Agua tranquila de bahía",
    },
    "Kite Beach": {
        "description": "Ensenada de fama mundial para el kiteboarding junto a Cabarete, con agua plana y una zona de olas mar afuera.",
        "access_description": "Justo al oeste de la bahía de Cabarete; acceso desde la carretera.",
        "ecosystem": "Zona de kite a sotavento con agua plana",
        "water_conditions": "Plana por dentro, con olas por fuera del arrecife",
    },
    "Playa Sosúa Bay (Charamicos)": {
        "description": "La entrada del lado local a la bahía de Sosúa, con agua tranquila de arrecife y operadores de tours en bote económicos.",
        "access_description": "Extremo oeste de la bahía de Sosúa, por el barrio de Charamicos.",
        "ecosystem": "Bahía de arrecife resguardada",
        "water_conditions": "Tranquila, protegida por el arrecife",
    },
    "Playa Diamante": {
        "description": "Laguna escondida en media luna cerca de Cabrera, de agua tranquila y poco profunda: ideal para familias.",
        "access_description": "Cerca de Cabrera; un camino corto lleva a la playa-laguna resguardada.",
        "ecosystem": "Laguna en media luna resguardada por un banco de arena",
        "water_conditions": "Laguna muy tranquila",
    },
    "Playa El Bretón": {
        "description": "Playa salvaje al pie de acantilados, dentro de una reserva científica costera de paisajes imponentes.",
        "access_description": "Dentro de la reserva Cabo Francés Viejo, cerca de Cabrera.",
        "ecosystem": "Reserva Científica Cabo Francés Viejo",
        "water_conditions": "Atlántico abierto; cuidado con las corrientes",
    },
    "Playa Las Galeras": {
        "description": "Playa relajada de pueblo pesquero y punto de salida de los botes hacia Rincón y Frontón.",
        "access_description": "Al final de la carretera, en la punta de la península de Samaná.",
        "ecosystem": "Bahía tranquila y costa de pueblo pesquero",
        "water_conditions": "Tranquila y poco profunda",
    },
    "Playa El Valle": {
        "description": "Playa imponente y poco visitada donde las montañas verdes se encuentran con el Atlántico, cerca de Samaná.",
        "access_description": "Carretera de montaña con muchas curvas al norte del pueblo de Samaná, o en bote.",
        "ecosystem": "Playa en desembocadura de río enmarcada por acantilados verdes",
        "water_conditions": "Oleaje y corrientes; báñate con cuidado",
    },
    "Playa Punta Popy": {
        "description": "La animada playa urbana de Las Terrenas, punto de encuentro local para el kitesurf y los tragos al atardecer.",
        "access_description": "A lo largo del bulevar costero de Las Terrenas.",
        "ecosystem": "Playa urbana ventosa",
        "water_conditions": "Ventosa, con oleaje corto y ligero",
    },
    "Playa Najayo": {
        "description": "Playa popular de fin de semana para los capitaleños, llena de comedores de mariscos al aire libre.",
        "access_description": "Al oeste de Santo Domingo, cerca de San Cristóbal; acceso por carretera.",
        "ecosystem": "Costa sur mixta de arena y piedra",
        "water_conditions": "Olas moderadas; algunas zonas rocosas",
    },
    "Playa Palenque": {
        "description": "Playa local sin pretensiones al suroeste de la capital, muy visitada por dominicanos de paseo.",
        "access_description": "Cerca de Nigua/Palenque, al suroeste de Santo Domingo.",
        "ecosystem": "Playa abierta de la costa sur",
        "water_conditions": "Olas caribeñas moderadas",
    },
    "Playa Palmar de Ocoa": {
        "description": "Playa de bahía tranquila en Azua, conocida por la pesca deportiva y sus villas silenciosas de fin de semana.",
        "access_description": "Bahía de Ocoa, saliendo de la carretera Sánchez en Azua.",
        "ecosystem": "Bahía resguardada de agua tranquila",
        "water_conditions": "Agua tranquila de bahía",
    },
    "Playa Boca de Yuma": {
        "description": "Pueblo pesquero agreste junto al Parque Nacional Cotubanamá, famoso por la pesca deportiva y las excursiones a cuevas.",
        "access_description": "Pueblo pesquero al borde del Parque Nacional Cotubanamá, costa sureste.",
        "ecosystem": "Costa de acantilados en el borde del Parque Nacional Cotubanamá",
        "water_conditions": "Costa agreste; más calmada en la desembocadura del río",
    },
    "Playa San Rafael": {
        "description": "Playa emblemática de Barahona donde un río frío de montaña forma piscinas naturales junto al mar.",
        "access_description": "Carretera costera al sur de la ciudad de Barahona.",
        "ecosystem": "Río de montaña que desemboca en el Caribe",
        "water_conditions": "Pozas frías de río junto al oleaje",
    },
    "Playa Los Patos": {
        "description": "Playa de piedras en Barahona, famosa por el río más corto del mundo y una laguna tranquila para bañarse.",
        "access_description": "A la orilla de la carretera costera Barahona–Paraíso.",
        "ecosystem": "Playa de piedras con laguna de agua dulce",
        "water_conditions": "Laguna tranquila junto al oleaje caribeño",
    },
    "Playa Quemaíto": {
        "description": "Ensenada apacible de Barahona, de agua excepcionalmente clara y entorno tranquilo y panorámico.",
        "access_description": "Camino corto saliendo de la carretera costera al sur de Barahona.",
        "ecosystem": "Ensenada de piedra y arena con agua clara",
        "water_conditions": "Clara, se hace honda rápido",
    },
    "Playa Saladilla": {
        "description": "Playa amplia y familiar de Barahona, de agua tranquila y con comedores locales de mariscos.",
        "access_description": "Al norte de la ciudad de Barahona, por la carretera costera.",
        "ecosystem": "Playa caribeña larga y abierta",
        "water_conditions": "Olas caribeñas suaves",
    },
    "Playa Juan de Bolaños": {
        "description": "La playa principal de Monte Cristi y base para los tours al Parque Nacional El Morro y a los Cayos Siete Hermanos.",
        "access_description": "Playa principal de Monte Cristi, en el extremo noroeste.",
        "ecosystem": "Puerta de entrada al Parque Nacional El Morro",
        "water_conditions": "Agua tranquila de bahía",
    },
}
