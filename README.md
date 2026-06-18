# rentshare

**Absentee-owner (landlord) share of residential parcels, by Chicago ward / community area / zip.**

A standalone civic-data metric (sibling to `chainshare`, `parkability`, `sunscore`) for
ward-wise-civic-tech / Penlight. Method adapted from
[smacmullan/nws-property-ownership-analysis](https://github.com/smacmullan/nws-property-ownership-analysis):
a residential parcel is **absentee-owned** (a rental, not owner-occupied) when its **taxpayer
mailing address differs from the property address**. We classify every Chicago residential
parcel that way and report the absentee share per geography.

## The metric

`absentee_owner_share_pct` — for each area, residential parcels whose taxpayer mailing address
differs from the property address ÷ all residential parcels × 100. Higher = more landlord/rental
ownership.

## How it works

Two Cook County Assessor open datasets (Socrata / SODA), joined on PIN — **no geometry needed**,
because the Parcel Universe already carries `ward_num` and `chicago_community_area_num`:

| Dataset | ID | Used for |
| --- | --- | --- |
| Assessor – Parcel Universe | `nj4t-kc8j` | PIN → ward / community area / zip / class / year |
| Assessor – Parcel Addresses | `3723-97qp` | PIN → property address + taxpayer mailing address |

Residential = property `class` starting with `2`. Addresses are normalized (uppercase, strip
punctuation/whitespace) before comparison, so `1 S State St.` matches `1 S STATE ST`.

## Run it

```bash
python -m rentshare                       # full Chicago, default year 2023
python -m rentshare --year 2023           # pick the assessment year
python -m rentshare --max-rows 100000     # bounded quick run (partial)
python -m rentshare --geo-input g.json --address-input a.json   # fully offline
python -m pytest                              # offline fixtures
```

No third-party dependencies (standard library only). Outputs to `data/processed/`:
`{ward,community_area,zip}_landlord_summary.{json,csv}` + `metadata.json`.

> **Note on the live pull.** The Cook County Socrata API is slow for the full ~600k-parcel
> join (a few minutes, occasionally timing out); the fetch retries with backoff and pages on
> the cheap `:id` order. For a stable full run, cache the parcel-geo map once
> (`--geo-input`) and re-run. Use a fully-populated assessment year — 2026 mailing addresses
> are still incomplete; **2023** is the current default.

## Caveats (kept honest)

Mailing-address mismatch is a *proxy* for rental, not a tenure census: inconsistent address
formatting yields some false absentees, and LLC/owner names can obscure true ownership. The
richer "landlord concentration" angle (grouping absentee parcels by taxpayer name to find large
owners) is a natural next layer. One signal, weight accordingly.

## Acknowledgments

The core methodology — flagging a residential parcel as absentee/landlord-owned when its
**taxpayer mailing address differs from the property address** — comes from **Sean MacMullan's**
[nws-property-ownership-analysis](https://github.com/smacmullan/nws-property-ownership-analysis),
which applied it to Chicago's Northwest Side. `rentshare` is a clean reimplementation that
generalizes that idea to **every ward, community area, and zip** citywide and publishes the
result as a Penlight metric source. Credit for the approach is entirely his; thanks, Sean.

## License

Code: MIT. Derived data: based on Cook County Assessor open data. The methodology is credited
above; this repository does not reuse the original project's code.
