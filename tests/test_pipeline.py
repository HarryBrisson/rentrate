import json
from pathlib import Path
from landlordshare.pipeline import run, normalize_address

FIX = Path(__file__).resolve().parent.parent / "data" / "fixtures"


def test_absentee_classification_and_shares(tmp_path):
    result = run(year=2023, geo_input=FIX / "sample_geo.json",
                 address_input=FIX / "sample_addresses.json", output_dir=tmp_path)
    by_ward = {r["area_id"]: r for r in result["summaries"]["ward"]}
    # Ward 01: A owner-occupied, B absentee -> 50%.
    assert by_ward["01"]["residential_parcels"] == 2
    assert by_ward["01"]["absentee_owned_parcels"] == 1
    assert by_ward["01"]["absentee_owner_share_pct"] == 50.0
    # Ward 02: C matches despite formatting (owner), D absentee (PO box) -> 50%.
    assert by_ward["02"]["absentee_owned_parcels"] == 1
    # Parcel Z has no geo row -> not counted.
    assert result["metadata"]["totals"]["matched_parcels"] == 4
    # Same metric flows to community area + zip.
    assert {r["area_id"] for r in result["summaries"]["community_area"]} == {"22", "08"}
    assert (tmp_path / "zip_landlord_summary.json").exists()


def test_address_normalization_ignores_case_and_punctuation():
    assert normalize_address("1 S State St.") == normalize_address("1 S STATE ST")
    assert normalize_address("PO BOX 99") != normalize_address("3 S STATE ST")
