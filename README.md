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

## Second metric: rent-as-profit (Sean's Southside Together idea) — built

A richer second metric Sean flagged — from a Southside Together tenant-organizing activity —
estimates **how much of a tenant's rent is landlord profit**, vs. property taxes, mortgage, and
upkeep. Each building's annual rent is decomposed exactly:

```
rent = property tax + mortgage (debt service) + operating costs + landlord profit
profit_share = profit ÷ rent
```

The per-building model is `rentrate/profit.py` (`estimate_building`); the citywide aggregation is
`rentrate/profit_pipeline.py`. Inputs, by how solid they are:

| Input | Source (confirmed live) | Confidence |
| --- | --- | --- |
| Property tax | Assessor **Assessed Values** (`uzyt-m557`) → AV × state equalizer × composite rate | ✅ grounded in actual assessed value |
| Units in building | Assessor **Characteristics** (`bcnq-qi2z`, `total_units`/`apts`) | ✅ good |
| Rent / unit | ACS **B25064** median gross rent by **ZCTA**, joined on the parcel's zip | ⚠️ area estimate, not lease data |
| Mortgage (debt service) | **Parcel Sales** (`wvhk-k5uv`) last arm's-length price × assumed LTV × mortgage constant | ⚠️ the big unknown — no public per-parcel mortgage |
| Operating costs | rule-of-thumb share of rent (excludes tax, which is broken out) | ⚠️ assumption |

Joining rent on **zip** (ACS ZCTA), not tract, keeps the whole thing geometry-free and
dependency-free, like the absentee pipeline. It runs over absentee-owned (rental) parcels only —
the tenants who actually pay rent.

**Two mortgage bases, because that lever dominates the answer.** The mortgage depends on what the
landlord owes, which we can't observe. So every area gets both:

- `landlord_profit_share_pct` — **today basis**: loan basis = assessed market value (a uniform
  "what would a landlord buying this today net" counterfactual; comparable across areas).
- `landlord_profit_share_actual_pct` — **actual basis**: loan basis = last sale price (models
  likely *real* debt, but recent buyers at today's prices show thin/negative profit).

The **gap between the two is the story**: where it's wide, profit is mostly a function of *when*
the landlord bought, not how much they charge. Every modeled lever (LTV, rate, term, equalizer,
tax rate, operating ratio, occupancy) lives in `ProfitAssumptions` and is overridable from the CLI.

```bash
python -m rentrate.profit_pipeline                          # full live pull (slow; ~5 datasets + ACS)
python -m rentrate.profit_pipeline --max-rows 100000        # bounded quick run
python -m rentrate.profit_pipeline --mortgage-rate 0.075 --loan-to-value 0.8   # tune assumptions
python -m rentrate.profit_pipeline --geo-input g.json --address-input a.json \
    --assessed-input v.json --units-input u.json --sales-input s.json --rent-input r.json  # offline
```

Outputs `{ward,community_area,zip}_profit_summary.{json,csv}` + `profit_metadata.json` (which
echoes the assumptions used). **Frame it honestly as an estimate / teaching tool**, exactly as the
original organizing activity does — a strong advocacy signal, a low-confidence number.

### Standalone calculator

`calculator/index.html` is the per-building "where does your rent go?" calculator Sean imagined —
a single self-contained page (no build, no dependencies) whose JS mirrors `profit.py`. Enter rent,
units, building value, and optionally what the landlord paid; it shows the rent split as a stacked
bar and, when a purchase price is given, both mortgage-basis scenarios side by side. Open the file
directly or serve the `calculator/` directory.

Still to do: wire the summaries into Penlight as a metric source (like `chainshare`/`parkability`)
— that needs a full live pull first.

## License

Code: MIT. Derived data: based on Cook County Assessor open data. The methodology is credited
above; this repository does not reuse the original project's code.
