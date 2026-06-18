"""Landlord / absentee-owner share of residential parcels, by Chicago ward / community area / zip.

Method (after smacmullan/nws-property-ownership-analysis): a residential parcel is
**absentee-owned** (a rental, not owner-occupied) when its **taxpayer mailing address
differs from the property address**. We classify every Chicago residential parcel that way
and report the absentee share per ward, community area, and zip.

Two Cook County Open Data (Socrata/SODA) datasets, joined on PIN — no geometry needed,
because the Parcel Universe already carries `ward_num` and `chicago_community_area_num`:

* Parcel Universe   (nj4t-kc8j) — PIN → ward / community area / zip / class / year
* Parcel Addresses  (3723-97qp) — PIN → property address + taxpayer mailing address

Caveats (kept honest): mailing-address formatting is inconsistent, so normalized-string
comparison yields some false absentees; LLC/owner names can obscure true ownership; and a
non-matching mailing address is a *proxy* for rental, not a tenure census. One signal, not
the whole truth.
"""

from __future__ import annotations

import csv
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PROCESSED = REPO_ROOT / "data" / "processed"
RAW = REPO_ROOT / "data" / "raw"

PARCEL_UNIVERSE = "https://datacatalog.cookcountyil.gov/resource/nj4t-kc8j.json"
PARCEL_ADDRESSES = "https://datacatalog.cookcountyil.gov/resource/3723-97qp.json"
SOURCE_PAGE = "https://datacatalog.cookcountyil.gov/Property-Taxation/Assessor-Parcel-Universe/nj4t-kc8j"
DEFAULT_YEAR = 2023  # last fully-populated assessment year for mailing addresses
PAGE = 50000
METRIC = "absentee_owner_share_pct"

GEOGRAPHIES = ("ward", "community_area", "zip")
_NONALNUM = re.compile(r"[^A-Z0-9]+")


def normalize_address(value: Any) -> str:
    return _NONALNUM.sub(" ", str(value or "").upper()).strip()


def _soda(url: str, params: dict[str, Any], timeout: float = 300.0, attempts: int = 4) -> list[dict[str, Any]]:
    import time
    request_url = f"{url}?{urllib.parse.urlencode(params)}"
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(request_url, headers={"User-Agent": "rentshare/0.1"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            last = error
            if attempt < attempts:
                time.sleep(3.0 * attempt)
    raise RuntimeError(f"SODA request failed after {attempts} attempts: {last}")


def _paginate(url: str, *, select: str, where: str, max_rows: int | None):
    # Order by the cheap Socrata `:id` (not a data column) so paging stays stable and fast.
    offset = 0
    while True:
        page = _soda(url, {"$select": select, "$where": where, "$limit": PAGE, "$offset": offset, "$order": ":id"})
        if not page:
            return
        for row in page:
            yield row
        offset += PAGE
        if len(page) < PAGE or (max_rows is not None and offset >= max_rows):
            return


def fetch_residential_geo(year: int, *, max_rows: int | None = None) -> dict[str, dict[str, str]]:
    """{pin: {ward, community_area, zip}} for Chicago residential (class 2xx) parcels."""
    where = f"year={year} AND ward_num IS NOT NULL AND starts_with(class,'2')"
    geo: dict[str, dict[str, str]] = {}
    for row in _paginate(PARCEL_UNIVERSE, select="pin,ward_num,chicago_community_area_num,zip_code",
                          where=where, max_rows=max_rows):
        pin = row.get("pin")
        ward = row.get("ward_num")
        if not pin or ward in (None, ""):
            continue
        geo[pin] = {
            "ward": f"{int(float(ward)):02d}",
            "community_area": f"{int(float(row['chicago_community_area_num'])):02d}"
            if row.get("chicago_community_area_num") not in (None, "") else None,
            "zip": (row.get("zip_code") or "")[:5] or None,
        }
    return geo


def aggregate(year: int, geo: dict[str, dict[str, str]], *, max_rows: int | None = None,
              address_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Stream Parcel Addresses, classify absentee, tally per geography."""
    accum = {g: defaultdict(lambda: {"residential": 0, "absentee": 0}) for g in GEOGRAPHIES}
    where = f"year={year} AND mail_address_full IS NOT NULL AND prop_address_full IS NOT NULL"
    rows = address_rows if address_rows is not None else _paginate(
        PARCEL_ADDRESSES, select="pin,prop_address_full,mail_address_full", where=where, max_rows=max_rows)
    matched = 0
    for row in rows:
        loc = geo.get(row.get("pin"))
        if loc is None:
            continue
        matched += 1
        is_absentee = normalize_address(row["mail_address_full"]) != normalize_address(row["prop_address_full"])
        for g in GEOGRAPHIES:
            area_id = loc.get(g)
            if area_id is None:
                continue
            bucket = accum[g][area_id]
            bucket["residential"] += 1
            if is_absentee:
                bucket["absentee"] += 1
    return {"accum": accum, "matched_parcels": matched}


def _rows(accum: dict[str, dict[str, int]], geo_key: str) -> list[dict[str, Any]]:
    rows = []
    for area_id, b in sorted(accum.items()):
        total = b["residential"]
        share = round(b["absentee"] / total * 100, 2) if total else None
        rows.append({
            "area_type": geo_key,
            "area_id": area_id,
            "residential_parcels": total,
            "absentee_owned_parcels": b["absentee"],
            METRIC: share,
        })
    return rows


def run(*, year: int = DEFAULT_YEAR, max_rows: int | None = None,
        geo_input: str | Path | None = None, address_input: str | Path | None = None,
        output_dir: str | Path = PROCESSED) -> dict[str, Any]:
    if geo_input:
        geo = json.loads(Path(geo_input).read_text())
        mode = f"cached-geo:{geo_input}"
    else:
        geo = fetch_residential_geo(year, max_rows=max_rows)
        mode = f"soda:{PARCEL_UNIVERSE}"
    address_rows = json.loads(Path(address_input).read_text()) if address_input else None
    result = aggregate(year, geo, max_rows=max_rows, address_rows=address_rows)
    accum = result["accum"]

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries = {}
    for geo_key in GEOGRAPHIES:
        rows = _rows(accum[geo_key], geo_key)
        summaries[geo_key] = rows
        (output_dir / f"{geo_key}_landlord_summary.json").write_text(json.dumps(rows, indent=2))
        with open(output_dir / f"{geo_key}_landlord_summary.csv", "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else
                                    ["area_type", "area_id", "residential_parcels", "absentee_owned_parcels", METRIC])
            writer.writeheader()
            writer.writerows(rows)

    metadata = {
        "metric_id": METRIC,
        "source": "Cook County Assessor — Parcel Universe + Parcel Addresses",
        "source_page": SOURCE_PAGE,
        "method": "absentee = normalized taxpayer mailing address != property address",
        "inspiration": "https://github.com/smacmullan/nws-property-ownership-analysis",
        "year": year,
        "mode": mode,
        "collected_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "totals": {
            "geo_parcels": len(geo),
            "matched_parcels": result["matched_parcels"],
            "citywide_absentee_share_pct": round(
                sum(b["absentee"] for b in accum["ward"].values())
                / max(1, sum(b["residential"] for b in accum["ward"].values())) * 100, 2),
        },
        "caveats": "Mailing-address formatting is inconsistent; absentee is a rental proxy, not a tenure census.",
    }
    (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))
    return {"summaries": summaries, "metadata": metadata}
