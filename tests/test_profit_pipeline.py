from pathlib import Path

from rentrate.profit_pipeline import METRIC_ACTUAL, METRIC_TODAY, run

FIX = Path(__file__).resolve().parent.parent / "data" / "fixtures"


def _run(tmp_path):
    return run(
        year=2023,
        geo_input=FIX / "sample_geo.json",
        address_input=FIX / "sample_addresses.json",
        assessed_input=FIX / "sample_assessed.json",
        units_input=FIX / "sample_units.json",
        sales_input=FIX / "sample_sales.json",
        rent_input=FIX / "sample_rent.json",
        output_dir=tmp_path,
    )


def test_only_absentee_rentals_are_counted(tmp_path):
    result = _run(tmp_path)
    # Of A,B,C,D only B (mail!=prop) and D (PO box) are absentee rentals; A and C are owner-occupied.
    assert result["metadata"]["totals"]["rental_parcels_used"] == 2
    by_ward = {r["area_id"]: r for r in result["summaries"]["ward"]}
    assert by_ward["01"]["rental_parcels"] == 1  # B
    assert by_ward["02"]["rental_parcels"] == 1  # D
    assert set(by_ward) == {"01", "02"}


def test_two_scenario_gap_shows_recent_buyers_pinched(tmp_path):
    result = _run(tmp_path)
    by_ward = {r["area_id"]: r for r in result["summaries"]["ward"]}
    # Ward 01 = B, bought recently for $525k: the "today" basis profits modestly, but the
    # "actual" (recent high purchase) basis is underwater — and today > actual.
    b = by_ward["01"]
    assert b[METRIC_TODAY] > 0
    assert b[METRIC_ACTUAL] < 0
    assert b[METRIC_TODAY] > b[METRIC_ACTUAL]


def test_no_sale_means_actual_equals_today(tmp_path):
    result = _run(tmp_path)
    by_ward = {r["area_id"]: r for r in result["summaries"]["ward"]}
    # Ward 02 = D, which has no sale row: both bases fall back to assessed-value, so the two
    # profit shares coincide.
    d = by_ward["02"]
    assert d[METRIC_TODAY] == d[METRIC_ACTUAL]


def test_metric_flows_to_all_three_geographies(tmp_path):
    result = _run(tmp_path)
    assert {r["area_id"] for r in result["summaries"]["community_area"]} == {"22", "08"}
    assert {r["area_id"] for r in result["summaries"]["zip"]} == {"60647", "60610"}
    assert (tmp_path / "ward_profit_summary.csv").exists()
    assert (tmp_path / "profit_metadata.json").exists()


def test_parcel_without_rent_for_its_zip_is_skipped(tmp_path):
    # Drop the rent for B's zip (60647): B can no longer be priced, so only D (zip 60610) remains.
    partial_rent = tmp_path / "rent.json"
    partial_rent.write_text('{"60610": 1800}')
    result = run(
        year=2023,
        geo_input=FIX / "sample_geo.json",
        address_input=FIX / "sample_addresses.json",
        assessed_input=FIX / "sample_assessed.json",
        units_input=FIX / "sample_units.json",
        sales_input=FIX / "sample_sales.json",
        rent_input=partial_rent,
        output_dir=tmp_path,
    )
    assert result["metadata"]["totals"]["rental_parcels_used"] == 1
    assert {r["area_id"] for r in result["summaries"]["ward"]} == {"02"}
