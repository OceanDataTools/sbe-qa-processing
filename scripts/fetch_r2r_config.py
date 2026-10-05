"""Writes a cruise TOML from R2R's catalog API, for a cruise and one of its filesets.

Usage: uv run python scripts/fetch_r2r_config.py CRUISE_ID FILESET_ID [--out configs]

Writes <out>/<CRUISE_ID>.toml.

Takes the cruise's name, vessel, operator, dates, ports and navigation bounding box from
service.rvdata.us/api/cruise and checks that the fileset belongs to the cruise. The API has no
port coordinates, country or state. For those, and for exact parity with an existing R2R QA
report, use `sbe-qa-processing config-from-r2r` on the report instead.
"""

import argparse
import json
import sys
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

from fetch_r2r_fileset import R2R, build_opener, fetch

from sbe_qa_processing.config import config_from_r2r_api, config_to_toml


def api_records(opener, endpoint: str, cruise_id: str) -> list[dict]:
    """Records from R2R's API, which answers "not found" with a 204 status in the body"""
    _, body = fetch(opener, f"{R2R}/api/{endpoint}/?cruise_id={urllib.parse.quote(cruise_id)}")
    return json.loads(body).get("data") or []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cruise_id")
    parser.add_argument("fileset_id", type=int)
    parser.add_argument("--out", type=Path, default=Path("configs"))
    parser.add_argument("--force", action="store_true", help="overwrite an existing --out")
    args = parser.parse_args()
    destination = args.out / f"{args.cruise_id}.toml"
    if destination.exists() and not args.force:
        print(f"{destination} exists; use --force to overwrite it", file=sys.stderr)
        return 1

    opener = build_opener()
    cruises = api_records(opener, "cruise", args.cruise_id)
    if not cruises:
        print(f"R2R has no cruise {args.cruise_id}", file=sys.stderr)
        return 1
    filesets = api_records(opener, "fileset", args.cruise_id)
    ctd = [f for f in filesets if f.get("device_type") == "ctd"]
    matches = [f for f in ctd if f.get("fileset_id") == args.fileset_id]
    if not matches:
        others = [f for f in filesets if f.get("fileset_id") == args.fileset_id]
        problem = (
            f"fileset {args.fileset_id} is {others[0].get('label')}"
            if others
            else f"has no fileset {args.fileset_id}"
        )
        listed = ", ".join(f"{f['fileset_id']} ({f.get('label')})" for f in ctd) or "none"
        print(f"{args.cruise_id} {problem}; its CTD filesets: {listed}", file=sys.stderr)
        return 1
    fileset = matches[0]

    config = config_from_r2r_api(cruises[0], fileset)
    lines = [f"# Cruise metadata from R2R's catalog API, {datetime.now(UTC):%Y-%m-%d}"]
    if config.extent is None:
        print(
            "warning: R2R has no navigation bounding box for this cruise; add [cruise.extent] "
            "by hand, or the Lat/Lon test is GREY (N)",
            file=sys.stderr,
        )
        lines.append("# R2R has no navigation bounding box for this cruise; add [cruise.extent]")
    text = "\n".join(lines) + "\n" + config_to_toml(config)

    destination.write_text(text)
    print(f"wrote {destination}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
