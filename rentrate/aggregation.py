"""Bring-your-own-polygons aggregation.

rentrate's headline pipeline tallies the absentee-owner share by joining parcels to ward / community
area / zip on the dataset's *built-in* geography columns — no coordinates. But the Assessor Parcel
Universe carries a `lat`/`lon` per parcel, so the share can instead be computed for **any** polygons:
fetch each residential parcel's point + absentee flag once, then point-in-polygon them into a caller's
cells and take `absentee ÷ residential` per cell.

This module is self-contained: a small standard-library ray-casting point-in-polygon (no shapely),
plus a fetch that pairs parcel coordinates with the absentee classification. The point-in-polygon code
here is original to this repo.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .pipeline import (
    DEFAULT_YEAR,
    PARCEL_ADDRESSES,
    PARCEL_UNIVERSE,
    _paginate,
    normalize_address,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

AGGREGATION_SPEC: dict[str, Any] = {
    "source": "rentrate",
    "source_url": "https://github.com/HarryBrisson/rentrate",
    "byop_metrics": {
        "absentee_owner_share_pct": {
            "unit": "percent",
            "combine": "share",
            "numerator": "absentee_owned_parcels",
            "denominator": "residential_parcels",
            "layer": "residential_parcels",
        },
    },
    "fixed_geography_metrics": {},
    "fine_layer": {
        "type": "geojson_points",
        "file": "residential_parcels.geojson",
        "value": "one point per Chicago residential parcel; properties.absentee = taxpayer mailing "
                 "address != property address (a rental proxy)",
    },
}


# --- standard-library point-in-polygon (ray casting), original to this repo -------------------------

def _point_in_ring(x: float, y: float, ring: list) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _point_in_polygon(x: float, y: float, polygon: list) -> bool:
    if not polygon or not _point_in_ring(x, y, polygon[0]):
        return False
    return not any(_point_in_ring(x, y, hole) for hole in polygon[1:])  # inside a hole -> outside


def _point_in_geometry(x: float, y: float, geometry: dict) -> bool:
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if kind == "Polygon":
        return _point_in_polygon(x, y, coords)
    if kind == "MultiPolygon":
        return any(_point_in_polygon(x, y, polygon) for polygon in coords)
    return False


def _geometry_bbox(geometry: dict) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []

    def walk(node: Any) -> None:
        if isinstance(node, (list, tuple)):
            if node and isinstance(node[0], (int, float)):
                xs.append(node[0])
                ys.append(node[1])
            else:
                for child in node:
                    walk(child)

    walk(geometry.get("coordinates"))
    return (min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 0.0, 0.0)


class _PolygonIndex:
    """Assign a lon/lat to the first target polygon containing it (bbox prefilter, then ray-cast)."""

    def __init__(self, features: list[dict], id_field: str):
        self.entries = []
        for feature in features:
            area_id = (feature.get("properties") or {}).get(id_field)
            geometry = feature.get("geometry")
            if area_id in (None, "") or not geometry:
                continue
            self.entries.append((str(area_id), _geometry_bbox(geometry), geometry))

    def assign(self, lng: float, lat: float) -> str | None:
        for area_id, (min_x, min_y, max_x, max_y), geometry in self.entries:
            if min_x <= lng <= max_x and min_y <= lat <= max_y and _point_in_geometry(lng, lat, geometry):
                return area_id
        return None


# --- aggregation -----------------------------------------------------------------------------------

def aggregate_to_polygons(
    target_geojson: dict[str, Any],
    id_field: str,
    name_field: str | None = None,
    *,
    parcel_points: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Point-in-polygon the residential parcels into ``target_geojson`` and return the absentee share.

    ``parcel_points`` is an iterable of ``{"lat", "lon", "absentee"}`` (e.g. from
    :func:`fetch_parcel_points` or the published ``residential_parcels.geojson``). Returns
    ``{area_id: {"absentee_owner_share_pct", "residential_parcels", "absentee_owned_parcels"}}``.
    """
    index = _PolygonIndex(target_geojson.get("features", []), id_field)
    accum: dict[str, list[int]] = {}
    for parcel in parcel_points:
        lat, lon = parcel.get("lat"), parcel.get("lon")
        if lat is None or lon is None:
            continue
        area_id = index.assign(float(lon), float(lat))
        if area_id is None:
            continue
        bucket = accum.setdefault(area_id, [0, 0])
        bucket[0] += 1
        if parcel.get("absentee"):
            bucket[1] += 1
    result: dict[str, dict[str, Any]] = {}
    for area_id, (residential, absentee) in accum.items():
        result[area_id] = {
            "absentee_owner_share_pct": round(absentee / residential * 100, 2) if residential else None,
            "residential_parcels": residential,
            "absentee_owned_parcels": absentee,
        }
    return result


def fetch_parcel_points(year: int = DEFAULT_YEAR, *, max_rows: int | None = None) -> list[dict[str, Any]]:
    """Fetch Chicago residential parcels as points with an absentee flag: pair each parcel's lat/lon
    (Assessor Parcel Universe) with mailing-vs-property address (Parcel Addresses)."""
    coords: dict[str, tuple[float, float]] = {}
    for row in _paginate(
        PARCEL_UNIVERSE,
        select="pin,lat,lon",
        where=f"year={year} AND starts_with(class,'2') AND lat IS NOT NULL AND lon IS NOT NULL",
        max_rows=max_rows,
    ):
        pin = row.get("pin")
        if pin and row.get("lat") not in (None, "") and row.get("lon") not in (None, ""):
            coords[pin] = (float(row["lat"]), float(row["lon"]))

    points: list[dict[str, Any]] = []
    for row in _paginate(
        PARCEL_ADDRESSES,
        select="pin,prop_address_full,mail_address_full",
        where=f"year={year} AND mail_address_full IS NOT NULL AND prop_address_full IS NOT NULL",
        max_rows=max_rows,
    ):
        coord = coords.get(row.get("pin"))
        if coord is None:
            continue
        absentee = normalize_address(row["mail_address_full"]) != normalize_address(row["prop_address_full"])
        points.append({"pin": row["pin"], "lat": coord[0], "lon": coord[1], "absentee": absentee})
    return points


def parcel_points_from_layer(layer_geojson: dict[str, Any]) -> list[dict[str, Any]]:
    """Read parcel points back from a published ``residential_parcels.geojson``."""
    points = []
    for feature in layer_geojson.get("features", []):
        coords = (feature.get("geometry") or {}).get("coordinates")
        if not coords:
            continue
        points.append({"lon": coords[0], "lat": coords[1], "absentee": bool((feature.get("properties") or {}).get("absentee"))})
    return points


def write_parcel_layer(output_dir: Path, parcel_points: Iterable[dict[str, Any]]) -> int:
    """Publish the parcel-point fine layer (+ spec) so a non-Python consumer can do the same."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    features = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
         "properties": {"absentee": bool(p["absentee"])}}
        for p in parcel_points
        if p.get("lat") is not None and p.get("lon") is not None
    ]
    (output_dir / "residential_parcels.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features})
    )
    (output_dir / "aggregation_spec.json").write_text(json.dumps(AGGREGATION_SPEC, indent=2))
    return len(features)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Absentee-owner share for your own polygons.")
    parser.add_argument("--polygons", type=Path, help="GeoJSON FeatureCollection of target areas")
    parser.add_argument("--id-field", default="area_id", help="property identifying each area")
    parser.add_argument("--parcel-layer", type=Path, help="residential_parcels.geojson (else fetch live)")
    parser.add_argument("--output", type=Path, help="write the {area_id: metrics} JSON here (else stdout)")
    parser.add_argument("--publish-layers", type=Path, help="fetch parcels and write the point layer + spec here")
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR)
    parser.add_argument("--max-rows", type=int, default=None, help="cap rows fetched (for a quick sample)")
    args = parser.parse_args(argv)

    if args.publish_layers:
        count = write_parcel_layer(args.publish_layers, fetch_parcel_points(args.year, max_rows=args.max_rows))
        print(f"wrote {count} parcel points + spec to {args.publish_layers}")
        return 0
    if not args.polygons:
        parser.error("pass --polygons (to aggregate) or --publish-layers (to export the parcel layer)")
    target = json.loads(args.polygons.read_text(encoding="utf-8"))
    if args.parcel_layer:
        points = parcel_points_from_layer(json.loads(args.parcel_layer.read_text(encoding="utf-8")))
    else:
        points = fetch_parcel_points(args.year, max_rows=args.max_rows)
    result = aggregate_to_polygons(target, args.id_field, parcel_points=points)
    payload = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(payload)
        print(f"wrote {len(result)} areas to {args.output}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
