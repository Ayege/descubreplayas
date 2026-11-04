# Data card: `dashboard/data/species_auto.json`

The species shown in the Vida lens of "Conoce esta playa". The file is generated
by `scripts/fetch_species.py`, which is run by hand and never by the 6-hour
pipeline.

The API details below were last checked on 2026-11-04. These APIs change, so
check them again before relying on this card.

## Licence and credit

This file is **not** covered by the CC BY 4.0 licence on the beach dataset.
Records keep the licence of their source.

| Source | Used for | Credit |
| --- | --- | --- |
| [iNaturalist](https://www.inaturalist.org) | Species, observation counts, common names, photos | Each photo is shown with its creator and its own licence |
| [OBIS](https://obis.org) | Whether a species is marine | Credited here and in `LICENSE-DATA.md` |

Only CC0, CC BY and CC BY-SA photos are stored, and only from iNaturalist's
open-data bucket (`inaturalist-open-data.s3.amazonaws.com`). The bucket also
holds CC BY-NC photos, so the licence is checked as well as the host.

**Photos are reference photos of the species, not photos taken at that beach.**
The caption says "Foto de la especie" so that it doesn't imply otherwise.

## Requests

Every response is capped at 4 MB and redirects are not followed. iNaturalist requests
are spaced about one per second, and OBIS requests 0.3 s apart. Only HTTP 429 and 5xx
are retried, at most 3 attempts, with exponential backoff, jitter and `Retry-After`.
A failed lookup isn't remembered, so the next beach tries it again.

### iNaturalist: species near a beach (one request per beach)

```
GET https://api.inaturalist.org/v1/observations/species_counts
  ?lat={lat}&lng={lon}&radius={km}&quality_grade=research&rank=species
  &photos=true&captive=false&geoprivacy=open&taxon_geoprivacy=open
  &per_page=200&locale=es
```

- `radius` is in kilometres: 2 by default, or the beach's `radius_km`.
- `per_page` has a documented maximum of 500. The largest response seen at 200
  was 245 KB.
- `english_common_name` matched a separate `locale=en` request on all 1,513
  comparisons, so one request per beach is enough.
- `threatened` is deliberately not sent. See "Sensitive species".

### iNaturalist: details per species (30 ids per request)

```
GET https://api.inaturalist.org/v1/taxa/{id,id,...}
```

- `conservation_statuses`: read for the privacy rule. Each entry has `geoprivacy`
  and a `place` with an `id`, or no place for a global status.
- `taxon_photos`: **not in the published API definition.** It is validated
  defensively: if it is missing or malformed, the species stays without a photo.
- The largest response seen was 1.74 MB.

### iNaturalist open-data bucket: thumbnails (top 8 species per beach)

```
GET https://inaturalist-open-data.s3.amazonaws.com/photos/{id}/small.{jpg|jpeg|png}
```

- `small` is 240 px on its longest side; `square` is only 75 px, too small for a 96 px file.
- Each is centre-cropped to 96×96 and re-encoded as WebP at quality 70. Only JPEG, PNG
  and WebP are decoded, downloads abort above 2 MB, and both the Content-Type and the
  file's first bytes are checked.
- This is the only download host. Photos with an allowed licence on any other host are
  counted in the run summary (`off`), never fetched. Redirects are refused.

### OBIS: marine tag (at most 10 names per request)

```
GET https://api.obis.org/v3/checklist?scientificname={name},{name},...
```

- No coordinates are sent.
- **Fields used:** `scientificName`, `acceptedNameUsage`, `is_marine`,
  `is_brackish`. These flags, and `is_terrestrial` and `is_freshwater`, appear in
  live responses but not in the published API definition.
- **Paging:** the definition lists no paging parameters for `/checklist`. A
  location query returned 10 of 114 results. Asking by name in batches of at most
  10 stays within that page, so no paging is needed. A `total` larger than the
  results returned is logged.
- **No match:** HTTP 500 with `NAME_NOT_FOUND` means no name in the batch matched.
  It is treated as "not marine" and is not retried.

## Fields

`taxa` holds each species once, keyed by iNaturalist id. `beaches` lists each
beach's species in ranked order, at most 12, with its `observation_count`.

| Field | Meaning |
| --- | --- |
| `marine` | True if OBIS sets `is_marine` or `is_brackish`. Stored as OBIS gave it |
| `marine_source` | `"obis"`, or null if the lookup failed and the answer is unknown |
| `marine_tagged_at` | Date of the OBIS lookup, or null if it failed |
| `photo.origin` | `default_photo` or `taxon_photos` |
| `photo.file`, `width`, `height`, `sha256` | The self-hosted copy under `dashboard/static/`: 96×96 WebP, named by its hash, with no metadata |
| `photo.attribution` | The creator's name, from that same photo object |
| `method`, `fetched_at` | How and when the record was generated |

The source link for each species is an iNaturalist observation search, built when
the file is loaded, from the beach's coordinates in `beaches_data.py`. The file
holds **no coordinates**, of observations or of beaches.

## Ranking

Pinned taxa first and in order, then marine with an allowed photo, then any allowed
photo, then the rest, by observation count inside each tier. After ranking, at most
1 plant and at most 2 of any other iconic group among the top 3. `hidden_taxa` is
removed first.

For ranking, "marine" means it lives in the sea or the intertidal zone. A coastal
tree whose seeds float does not count.

## Sensitive species

Privacy follows iNaturalist's own flags, not IUCN categories. A species is excluded
if either of these is true:

1. All its observations near the beach have `obscured` or `private` geoprivacy.
   `geoprivacy=open` and `taxon_geoprivacy=open` handle this on the server.
2. Its `conservation_statuses` has `geoprivacy` of `obscured` or `private`, with no
   place (global), the Dominican Republic (6931), or the larger place containing it
   (97394, from the API's `ancestor_place_ids`).

If the statuses can't be read, the species is excluded. This is the opposite of
photos on purpose: a missing photo costs nothing, but a missing privacy answer could
reveal a sensitive species. The run summary counts these failed lookups separately,
and if every lookup for a beach fails, that beach keeps its existing data.

## Known limitations

- **The marine tag is imperfect.** OBIS marks some land species as marine, because
  they grow on the coast. `MARINE_OVERRIDES` in `beach_profiles.py` corrects these.
  So far: *Cocos nucifera*, *Barringtonia asiatica*, *Hernandia sonora*,
  *Cassytha filiformis* and *Rhinella marina*.
- **Skipped beaches.** `SKIP_BEACHES` in `scripts/fetch_species.py` lists beaches left
  out of the run, with the reason. Playa Maimón is skipped until its coordinates are
  confirmed.
- **A 2 km radius reaches inland**, so land species appear at many beaches.
- **Species with no observations in the top 200 are not seen.** Marine tags are
  looked up only for species with a photo, plus any that make the list.
- **Common names** come from iNaturalist and aren't always Dominican usage. Some
  species have no Spanish name and show the scientific name.
- **Neighbouring beaches** can have nearly identical lists.
