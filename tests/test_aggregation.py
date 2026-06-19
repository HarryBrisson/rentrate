"""Bring-your-own-polygons aggregation: absentee-owner share for arbitrary polygons via parcel points."""
from __future__ import annotations

import json

from rentrate.aggregation import (
    AGGREGATION_SPEC,
    aggregate_to_polygons,
    parcel_points_from_layer,
    write_parcel_layer,
)

# A box over ~41.90-41.92 N, -87.69--87.67 W.
TARGET = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"cid": "Z1"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-87.69, 41.90], [-87.67, 41.90], [-87.67, 41.92], [-87.69, 41.92], [-87.69, 41.90]]],
            },
        }
    ],
}

PARCELS = [
    {"lat": 41.910, "lon": -87.680, "absentee": True},
    {"lat": 41.911, "lon": -87.681, "absentee": False},
    {"lat": 41.912, "lon": -87.682, "absentee": True},
    {"lat": 40.000, "lon": -88.000, "absentee": True},  # outside the box -> excluded
]


def test_aggregate_to_polygons_absentee_share():
    result = aggregate_to_polygons(TARGET, "cid", parcel_points=PARCELS)

    assert set(result) == {"Z1"}
    cell = result["Z1"]
    assert cell["residential_parcels"] == 3  # the far-away parcel is excluded
    assert cell["absentee_owned_parcels"] == 2
    assert cell["absentee_owner_share_pct"] == round(2 / 3 * 100, 2)  # 66.67


def test_parcel_layer_round_trips(tmp_path):
    count = write_parcel_layer(tmp_path, PARCELS)
    assert count == 4
    layer = json.loads((tmp_path / "residential_parcels.geojson").read_text())
    points = parcel_points_from_layer(layer)
    # re-aggregating from the published layer gives the same answer
    result = aggregate_to_polygons(TARGET, "cid", parcel_points=points)
    assert result["Z1"]["absentee_owner_share_pct"] == round(2 / 3 * 100, 2)
    assert (tmp_path / "aggregation_spec.json").exists()


def test_spec_declares_share_metric_byop():
    assert set(AGGREGATION_SPEC["byop_metrics"]) == {"absentee_owner_share_pct"}
    assert AGGREGATION_SPEC["byop_metrics"]["absentee_owner_share_pct"]["combine"] == "share"
    assert AGGREGATION_SPEC["fixed_geography_metrics"] == {}
