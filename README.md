# rentrate

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
python -m rentrate                       # full Chicago, default year 2023
python -m rentrate --year 2023           # pick the assessment year
python -m rentrate --max-rows 100000     # bounded quick run (partial)
python -m rentrate --geo-input g.json --address-input a.json   # fully offline
python -m pytest                              # offline fixtures
```

No third-party dependencies (standard library only). Outputs to `data/processed/`:
`{ward,community_area,zip}_rent_summary.{json,csv}` + `metadata.json`.

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

This metric stands on a lineage of Chicago civic-tech work, and credit belongs to that whole chain:

- The core method — flagging a residential parcel as absentee/landlord-owned when its
  **taxpayer mailing address differs from the property address** — comes from **Sean MacMullan's**
  [nws-property-ownership-analysis](https://github.com/smacmullan/nws-property-ownership-analysis),
  which applied it to Chicago's Northwest Side for housing-advocacy outreach.
- Sean's work was in turn **inspired by the Landlord Mapper project** presented at Chi Hack Night,
  which pioneered using Cook County property records to map landlord ownership.

`rentrate` is a clean reimplementation that generalizes the method to **every ward, community
area, and zip** citywide and publishes it as a Penlight metric source. The approach is theirs,
not ours — thank you, Sean and the Landlord Mapper team.

## Roadmap: rent-as-profit (Sean's Southside Together idea)

A richer second metric Sean flagged — from a Southside Together tenant-organizing activity —
estimates **how much of a tenant's rent is landlord profit**, vs. the landlord's property taxes
and mortgage. Per building:

```
profit ≈ rent collected − property taxes − mortgage (debt service) − operating costs
profit_share = profit ÷ rent collected
```

Inputs, by how solid they are:

| Input | Source | Confidence |
| --- | --- | --- |
| Property tax billed / PIN | Cook County **Treasurer / Assessor** | ✅ actual, per-parcel |
| Units in building | Assessor improvement characteristics (Sean's repo uses these) | ✅ good |
| Rent / unit | ACS **B25064** median gross rent by area, bedroom-adjusted | ⚠️ area estimate, not lease data |
| Mortgage (debt service) | last sale price (Cook County **sales**) × assumed LTV × mortgage constant | ⚠️ the big unknown — no public per-parcel mortgage |
| Operating costs | rule-of-thumb (e.g. the real-estate "50% rule") | ⚠️ assumption |

**Frame it honestly as an estimate / teaching tool**, exactly as the original organizing activity
does — the mortgage and rent inputs are modeled, not observed. Aggregated to ward / community area
/ zip it becomes "estimated landlord profit share," a strong advocacy signal but a low-confidence
one (label accordingly). It also fits a standalone calculator site, which is how Sean imagined it.

## License

Code: MIT. Derived data: based on Cook County Assessor open data. The methodology is credited
above; this repository does not reuse the original project's code.
