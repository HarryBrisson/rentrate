"""Estimated landlord-profit share of rent, aggregated to Chicago ward / community area / zip.

The per-building model lives in :mod:`rentrate.profit`; this module joins the Cook County open
datasets needed to feed it for every absentee-owned (rental) residential parcel, then rolls the
dollar amounts up per geography and reports the profit share of rent.

Because the mortgage basis is the model's most uncertain lever (see ``profit.py``), every area
gets **two** figures, per Harry's call:

* ``profit_share_today_basis``  — loan basis = the parcel's assessed market value (a uniform
  "what would a landlord buying this today net" counterfactual; comparable across areas).
* ``profit_share_actual_basis`` — loan basis = the parcel's last arm's-length sale price (models
  likely *real* debt, but biased toward recent buyers showing thin or negative profit).

The gap between the two is itself the story: where it is wide, profit is mostly a function of
*when* landlords bought, not how much they charge.

Datasets (Socrata/SODA, joined on PIN) + ACS for rent:

* Parcel Universe   (nj4t-kc8j) — PIN → ward / community area / zip / class      [via pipeline.py]
* Parcel Addresses  (3723-97qp) — PIN → property vs taxpayer mailing (absentee)  [via pipeline.py]
* Assessed Values   (uzyt-m557) — PIN → assessed value (tax + market-value basis)
* Characteristics   (bcnq-qi2z) — PIN → units in building
* Parcel Sales      (wvhk-k5uv) — PIN → last arm's-length sale price (mortgage basis)
* ACS B25064 (Census API)       — median gross rent by ZCTA → joined on the parcel's zip

Geometry-free and dependency-free by design (rent joins on zip, not tract), so it stays offline-
testable and consistent with the absentee pipeline. Live pulls are large; cache inputs and re-run.
"""

from __future__ import annotations

import csv
import json
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .pipeline import (
    GEOGRAPHIES,
    PARCEL_ADDRESSES,
    PROCESSED,
    _paginate,
    _soda,
    fetch_residential_geo,
    normalize_address,
)
from .profit import ProfitAssumptions, estimate_building

ASSESSED_VALUES = "https://datacatalog.cookcountyil.gov/resource/uzyt-m557.json"
CHARACTERISTICS = "https://datacatalog.cookcountyil.gov/resource/bcnq-qi2z.json"
PARCEL_SALES = "https://datacatalog.cookcountyil.gov/resource/wvhk-k5uv.json"
ACS_B25064 = "https://api.census.gov/data/{year}/acs/acs5"

DEFAULT_YEAR = 2023
DEFAULT_ACS_YEAR = 2023
METRIC_TODAY = "landlord_profit_share_pct"
METRIC_ACTUAL = "landlord_profit_share_actual_pct"


def _to_float(value: Any) -> float | None:
    try:
        f = float(value)
        return f if f == f else None  # reject NaN
    except (TypeError, ValueError):
        return None


def fetch_assessed_values(year: int, *, max_rows: int | None = None) -> dict[str, float]:
    """{pin: assessed_value} for residential (class 2xx) parcels — prefers certified, then board,
    then mailed total assessed value."""
    where = f"year={year} AND starts_with(class,'2')"
    out: dict[str, float] = {}
    for row in _paginate(ASSESSED_VALUES, select="pin,certified_tot,board_tot,mailed_tot",
                          where=where, max_rows=max_rows):
        pin = row.get("pin")
        av = _to_float(row.get("certified_tot")) or _to_float(row.get("board_tot")) or _to_float(row.get("mailed_tot"))
        if pin and av and av > 0:
            out[pin] = av
    return out


def fetch_units(year: int, *, max_rows: int | None = None) -> dict[str, int]:
    """{pin: units} from residential characteristics (total_units, else apts; default 1)."""
    out: dict[str, int] = {}
    for row in _paginate(CHARACTERISTICS, select="pin,total_units,apts", where=f"tax_year={year}",
                         max_rows=max_rows):
        pin = row.get("pin")
        if not pin:
            continue
        units = _to_float(row.get("total_units")) or _to_float(row.get("apts")) or 1
        out[pin] = max(1, int(units))
    return out


def fetch_last_sale(*, max_rows: int | None = None) -> dict[str, float]:
    """{pin: last arm's-length sale price}. Keeps the most recent qualifying sale per PIN,
    excluding sub-$10k, multi-parcel, and same-sale-within-365-day rows (Cook County's own
    arm's-length filter flags)."""
    where = ("sale_price > 10000 AND is_multisale='false' "
             "AND sale_filter_less_than_10k='false' AND sale_filter_same_sale_within_365='false'")
    latest: dict[str, tuple[str, float]] = {}
    for row in _paginate(PARCEL_SALES, select="pin,sale_price,sale_date", where=where, max_rows=max_rows):
        pin = row.get("pin")
        price = _to_float(row.get("sale_price"))
        date = row.get("sale_date") or ""
        if not pin or not price or price <= 0:
            continue
        if pin not in latest or date > latest[pin][0]:
            latest[pin] = (date, price)
    return {pin: price for pin, (_date, price) in latest.items()}


def fetch_zcta_rent(acs_year: int, *, api_key: str | None = None) -> dict[str, float]:
    """{zip: median gross rent} from ACS 5-year B25064 by ZCTA (the parcel's zip joins directly).
    No key needed for this volume; pass one to be safe under heavy use."""
    params = {"get": "B25064_001E", "for": "zip code tabulation area:*"}
    if api_key:
        params["key"] = api_key
    url = f"{ACS_B25064.format(year=acs_year)}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "rentrate/0.1"})
    with urllib.request.urlopen(request, timeout=120) as response:
        table = json.loads(response.read().decode("utf-8"))
    out: dict[str, float] = {}
    for value, zcta in table[1:]:  # row 0 is the header
        rent = _to_float(value)
        if rent and rent > 0:
            out[str(zcta)[:5]] = rent
    return out


def absentee_pins(year: int, geo: dict[str, dict[str, str]], *, max_rows: int | None = None,
                  address_rows: list[dict[str, Any]] | None = None) -> set[str]:
    """The set of residential PINs whose taxpayer mailing address differs from the property
    address — i.e. rentals — restricted to PINs we have geo for."""
    where = f"year={year} AND mail_address_full IS NOT NULL AND prop_address_full IS NOT NULL"
    rows = address_rows if address_rows is not None else _paginate(
        PARCEL_ADDRESSES, select="pin,prop_address_full,mail_address_full", where=where, max_rows=max_rows)
    out: set[str] = set()
    for row in rows:
        pin = row.get("pin")
        if pin not in geo:
            continue
        if normalize_address(row["mail_address_full"]) != normalize_address(row["prop_address_full"]):
            out.add(pin)
    return out


def _blank_area() -> dict[str, float]:
    return {"rental_parcels": 0, "annual_rent": 0.0, "profit_today": 0.0, "profit_actual": 0.0}


def aggregate_profit(
    *,
    pins: set[str],
    geo: dict[str, dict[str, str]],
    assessed: dict[str, float],
    units: dict[str, int],
    sales: dict[str, float],
    rent_by_zip: dict[str, float],
    assumptions: ProfitAssumptions | None = None,
) -> dict[str, dict[str, dict[str, float]]]:
    """Run both scenarios per rental parcel and sum the dollars into each geography."""
    accum = {g: defaultdict(_blank_area) for g in GEOGRAPHIES}
    used = 0
    for pin in pins:
        loc = geo.get(pin)
        av = assessed.get(pin)
        zip_code = loc.get("zip") if loc else None
        rent = rent_by_zip.get(zip_code) if zip_code else None
        if not (loc and av and rent):
            continue  # need geo + assessed value + a rent for the parcel's zip
        n_units = units.get(pin, 1)
        today = estimate_building(monthly_rent_per_unit=rent, units=n_units, assessed_value=av,
                                  sale_price=None, assumptions=assumptions)
        actual = estimate_building(monthly_rent_per_unit=rent, units=n_units, assessed_value=av,
                                   sale_price=sales.get(pin), assumptions=assumptions)
        used += 1
        for g in GEOGRAPHIES:
            area_id = loc.get(g)
            if area_id is None:
                continue
            bucket = accum[g][area_id]
            bucket["rental_parcels"] += 1
            bucket["annual_rent"] += today["annual_rent"]
            bucket["profit_today"] += today["profit"]
            bucket["profit_actual"] += actual["profit"]
    return {"accum": accum, "used_parcels": used}


def _rows(accum: dict[str, dict[str, float]], geo_key: str) -> list[dict[str, Any]]:
    rows = []
    for area_id, b in sorted(accum.items()):
        rent = b["annual_rent"]
        rows.append({
            "area_type": geo_key,
            "area_id": area_id,
            "rental_parcels": int(b["rental_parcels"]),
            "annual_rent_total": round(rent, 2),
            METRIC_TODAY: round(b["profit_today"] / rent * 100, 2) if rent else None,
            METRIC_ACTUAL: round(b["profit_actual"] / rent * 100, 2) if rent else None,
            "estimated_profit_total_today": round(b["profit_today"], 2),
            "estimated_profit_total_actual": round(b["profit_actual"], 2),
        })
    return rows


def run(
    *,
    year: int = DEFAULT_YEAR,
    acs_year: int = DEFAULT_ACS_YEAR,
    max_rows: int | None = None,
    geo_input: str | Path | None = None,
    address_input: str | Path | None = None,
    assessed_input: str | Path | None = None,
    units_input: str | Path | None = None,
    sales_input: str | Path | None = None,
    rent_input: str | Path | None = None,
    census_api_key: str | None = None,
    assumptions: ProfitAssumptions | None = None,
    output_dir: str | Path = PROCESSED,
) -> dict[str, Any]:
    """Build the per-geography rent-as-profit summaries. Any ``*_input`` path supplies that
    dataset from a cached JSON file (so the whole pipeline runs offline); otherwise it is fetched
    live. ``rent_input`` is a ``{zip: median_gross_rent}`` map."""
    load = lambda p: json.loads(Path(p).read_text())  # noqa: E731

    geo = load(geo_input) if geo_input else fetch_residential_geo(year, max_rows=max_rows)
    address_rows = load(address_input) if address_input else None
    pins = absentee_pins(year, geo, max_rows=max_rows, address_rows=address_rows)
    assessed = load(assessed_input) if assessed_input else fetch_assessed_values(year, max_rows=max_rows)
    units = load(units_input) if units_input else fetch_units(year, max_rows=max_rows)
    sales = load(sales_input) if sales_input else fetch_last_sale(max_rows=max_rows)
    rent_by_zip = load(rent_input) if rent_input else fetch_zcta_rent(acs_year, api_key=census_api_key)

    result = aggregate_profit(pins=pins, geo=geo, assessed=assessed, units=units, sales=sales,
                              rent_by_zip=rent_by_zip, assumptions=assumptions)
    accum = result["accum"]

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries = {}
    fieldnames = ["area_type", "area_id", "rental_parcels", "annual_rent_total",
                  METRIC_TODAY, METRIC_ACTUAL, "estimated_profit_total_today", "estimated_profit_total_actual"]
    for geo_key in GEOGRAPHIES:
        rows = _rows(accum[geo_key], geo_key)
        summaries[geo_key] = rows
        (output_dir / f"{geo_key}_profit_summary.json").write_text(json.dumps(rows, indent=2))
        with open(output_dir / f"{geo_key}_profit_summary.csv", "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    a = assumptions or ProfitAssumptions()
    total_rent = sum(b["annual_rent"] for b in accum["ward"].values())
    metadata = {
        "metric_ids": [METRIC_TODAY, METRIC_ACTUAL],
        "source": "Cook County Assessor (parcel universe, assessed values, characteristics, sales) + ACS B25064",
        "method": "per-building rent decomposition (profit.py); two mortgage bases (today / actual sale)",
        "model": "rent = property_tax + mortgage + operating_costs + profit; profit_share = profit / rent",
        "year": year,
        "acs_year": acs_year,
        "assumptions": vars(a),
        "collected_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "totals": {
            "rental_parcels_used": result["used_parcels"],
            "annual_rent_total": round(total_rent, 2),
            "citywide_profit_share_today_pct": round(
                sum(b["profit_today"] for b in accum["ward"].values()) / total_rent * 100, 2) if total_rent else None,
            "citywide_profit_share_actual_pct": round(
                sum(b["profit_actual"] for b in accum["ward"].values()) / total_rent * 100, 2) if total_rent else None,
        },
        "caveats": "Rent (ACS zip median), mortgage (modeled from sale price/value), and operating "
                   "costs (rule of thumb) are estimates; property tax is grounded in assessed value. "
                   "An advocacy/teaching estimate, not a tenure or income audit.",
    }
    (output_dir / "profit_metadata.json").write_text(json.dumps(metadata, indent=2))
    return {"summaries": summaries, "metadata": metadata}


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="rentrate.profit_pipeline",
        description="Estimated landlord-profit share of rent by Chicago ward/community-area/zip "
                    "(two mortgage bases: today vs actual sale).")
    p.add_argument("--year", type=int, default=DEFAULT_YEAR, help="Assessment year.")
    p.add_argument("--acs-year", type=int, default=DEFAULT_ACS_YEAR, help="ACS 5-year vintage for rent.")
    p.add_argument("--max-rows", type=int, default=None, help="Bound each SODA pull (quick partial run).")
    p.add_argument("--census-api-key", default=None, help="Optional Census API key for the rent pull.")
    p.add_argument("--output-dir", default=str(PROCESSED))
    for name in ("geo", "address", "assessed", "units", "sales", "rent"):
        p.add_argument(f"--{name}-input", help=f"Cached {name} JSON (offline / reuse).")
    # Assumption overrides — any lever in ProfitAssumptions.
    for lever in vars(ProfitAssumptions()):
        p.add_argument(f"--{lever.replace('_', '-')}", type=float, default=None,
                       help=f"Override ProfitAssumptions.{lever}.")
    a = p.parse_args(argv)

    overrides = {lever: getattr(a, lever) for lever in vars(ProfitAssumptions())
                 if getattr(a, lever) is not None}
    if "mortgage_term_years" in overrides:
        overrides["mortgage_term_years"] = int(overrides["mortgage_term_years"])
    assumptions = ProfitAssumptions(**overrides) if overrides else None

    result = run(
        year=a.year, acs_year=a.acs_year, max_rows=a.max_rows, census_api_key=a.census_api_key,
        geo_input=a.geo_input, address_input=a.address_input, assessed_input=a.assessed_input,
        units_input=a.units_input, sales_input=a.sales_input, rent_input=a.rent_input,
        assumptions=assumptions, output_dir=a.output_dir)
    print(json.dumps(result["metadata"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
