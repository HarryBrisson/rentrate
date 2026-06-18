"""CLI: python -m rentshare [--year 2023] [--max-rows N] [--geo-input f]"""
from __future__ import annotations
import argparse, json
from .pipeline import DEFAULT_YEAR, PROCESSED, run

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rentshare",
        description="Absentee-owner (landlord) share of Chicago residential parcels by ward/community-area/zip.")
    p.add_argument("--year", type=int, default=DEFAULT_YEAR)
    p.add_argument("--max-rows", type=int, default=None,
                   help="Bound each dataset's pull (for a quick partial run); omit for full city.")
    p.add_argument("--geo-input", help="Cached parcel-geo JSON {pin: {ward,community_area,zip}}.")
    p.add_argument("--output-dir", default=str(PROCESSED))
    a = p.parse_args(argv)
    result = run(year=a.year, max_rows=a.max_rows, geo_input=a.geo_input, output_dir=a.output_dir)
    print(json.dumps(result["metadata"], indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
