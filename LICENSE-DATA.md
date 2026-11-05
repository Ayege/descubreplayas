# Data license — CC BY 4.0

The **code** in this repository is licensed under the Apache License 2.0
(see [`LICENSE`](LICENSE)). The **beach dataset** is not: it is an original
compilation and is licensed separately.

## What this covers

| File | Contents |
| --- | --- |
| `dashboard/beaches_data.py` | Catalogue of 55 Dominican Republic beaches — coordinates, province, region, access type and description, entrance fee, parking, beach type, activities, wildlife, ecosystem, facilities, water conditions, best time to visit |
| `dashboard/beaches_i18n.py` | Spanish translations of that catalogue — 183 vocabulary terms plus per-beach prose for all 55 beaches |
| `dashboard/beach_profiles.py` | Hand-written "Conoce esta playa" content: teasers, species notes, geology and history for each beach, with sources |
| `sql/schema.sql` (seed rows) | The zone and beach seed data derived from the above |

`dashboard/data/species_auto.json` is **not** covered by this licence. It holds
third-party records from [iNaturalist](https://www.inaturalist.org), which keep
their own licences, and each photo in it is shown with its own attribution and
licence. The marine tag on each species comes from [OBIS](https://obis.org). See
[its data card](docs/data-cards/species_auto.md).

Images under `dashboard/static/` keep the licence of their originals, listed per
image in `dashboard/data/beach_photos.json` and `dashboard/data/species_auto.json`.
We resize and re-encode them; images derived from CC BY-SA originals stay CC BY-SA.

Everything else in the repository — the pipeline, the API, the dashboard
code, the drift and ML models — is Apache 2.0.

## Licence

These files are licensed under the
**[Creative Commons Attribution 4.0 International License][cc-by]**
(CC BY 4.0).

You are free to **share** (copy and redistribute in any medium or format)
and **adapt** (remix, transform, and build upon the material) for any
purpose, including commercially.

Under one condition: **attribution**. You must give appropriate credit,
provide a link to the licence, and indicate if changes were made. You may
do so in any reasonable manner, but not in any way that suggests the
licensor endorses you or your use.

The full legal text is at
<https://creativecommons.org/licenses/by/4.0/legalcode>.

## How to attribute

Copy this line into your credits, documentation, or data README:

> Beach data from **Descubre Playas RD** by Ayesha Yege, licensed under
> [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
> Source: <https://descubreplayas.com.do>

If you modified the data, say so — for example, "adapted from" instead of
"from".

## Where the underlying facts come from

The compilation is original; many of the individual facts are not. They were
assembled from Dominican Republic Ministry of Tourism guides, national-park
data, and travel references. Coordinates are approximate beach centroids.

Amenity, fee and access notes are local observations that change over time.
Treat them as a starting point rather than an authority, and do not rely on
the sargassum forecast alone for safety-critical decisions — see
[Limitations and known gaps](README.md#limitations-and-known-gaps).

Live sargassum detections, ocean currents, wind and map tiles come from
third-party services under their own terms and are **not** covered by this
licence. See [`NOTICE`](NOTICE) for that list.

[cc-by]: https://creativecommons.org/licenses/by/4.0/
