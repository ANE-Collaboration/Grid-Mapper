"""Collect real TSO data into an immutable ISO-week snapshot and static index."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import os
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline.collect_tso import collect_all
from pipeline.extract_osm import clean_osm_voltage, fetch_osm_power_lines, DEFAULT_OUTPUT_GEOJSON
from pipeline.match_capacity import match_lines

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history"
LOG = logging.getLogger(__name__)


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def validate_national_source(data):
    metadata = data.get("metadata", {})
    if metadata.get("is_demo") is not False or metadata.get("source") != "OpenStreetMap / Overpass":
        raise ValueError("A verified live OSM download is required; fixture or unverified cache cannot be published")
    if len(data.get("features", [])) < 1000:
        raise ValueError("Nationwide download has fewer than 1,000 segments; refusing to publish a partial dataset")


def pack_geometry(spatial):
    validate_national_source(spatial)
    lines, seen = [], set()
    for feature in spatial["features"]:
        props, geometry = feature["properties"], feature["geometry"]
        identifier = props["osm_id"]
        if identifier in seen or geometry["type"] != "LineString" or len(geometry["coordinates"]) < 2:
            raise ValueError(f"Invalid or duplicate OSM way: {identifier}")
        seen.add(identifier)
        for coordinate in geometry["coordinates"]:
            if len(coordinate) != 2 or not all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in coordinate):
                raise ValueError(f"Invalid coordinate for OSM way {identifier}")
            if not -180 <= coordinate[0] <= 180 or not -90 <= coordinate[1] <= 90:
                raise ValueError(f"Coordinate outside geographic bounds: {identifier}")
        lines.append([identifier, props.get("name", ""), props.get("operator", ""),
                      clean_osm_voltage(props.get("voltage_raw", "")), props.get("power", "line"), geometry["coordinates"]])
    return {"schema_version": 1, "source": spatial["metadata"], "lines": sorted(lines, key=lambda line: line[0])}


def write_geometry(history, geometry):
    data = encode(geometry)
    digest = hashlib.sha256(data).hexdigest()
    filename = f"geometry/{digest}.json.gz"
    target = history / filename
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(data, compresslevel=9, mtime=0))
    return filename


def rebuild_index(history):
    snapshots = []
    for path in sorted((history / "snapshots").glob("*.json"), reverse=True):
        snapshot = json.loads(path.read_bytes())
        snapshots.append({"week": snapshot["week"], "url": f"snapshots/{path.name}",
                          "created_at": snapshot["created_at"], "stats": snapshot["stats"]})
    index = {"schema_version": 1, "latest": snapshots[0]["week"] if snapshots else None, "snapshots": snapshots}
    temporary = history / "index.json.tmp"
    temporary.write_bytes(encode(index))
    os.replace(temporary, history / "index.json")
    return index


def create_snapshot(history=HISTORY, geometry=None, refresh_geometry=False, now=None, collector=collect_all):
    history = Path(history)
    now = now or datetime.now(ZoneInfo("Asia/Tokyo"))
    year, week, _ = now.isocalendar()
    week_id = f"{year}-W{week:02d}"
    target = history / "snapshots" / f"{week_id}.json"
    if target.exists():
        rebuild_index(history)  # Also repairs an interrupted index publication.
        LOG.info("%s already exists; preserved without modification", week_id)
        return json.loads(target.read_bytes())
    existing = sorted((history / "snapshots").glob("*.json"))
    previous = json.loads(existing[-1].read_bytes()) if existing else None
    if previous and previous["week"] >= week_id:
        raise ValueError("Cannot backfill a past week using today's observations")
    if geometry is None:
        if previous and not refresh_geometry:
            geometry = json.loads(gzip.decompress((history / previous["geometry"]).read_bytes()))
        else:
            if not refresh_geometry and DEFAULT_OUTPUT_GEOJSON.exists():
                spatial = json.loads(DEFAULT_OUTPUT_GEOJSON.read_bytes())
            else:
                spatial = fetch_osm_power_lines()
            geometry = pack_geometry(spatial)
    if previous and len(geometry["lines"]) < previous["stats"]["line_count"] * 0.9:
        raise ValueError("OSM line count dropped more than 10%; review before replacing nationwide geometry")
    utilities = collector(history / "sources", previous["utilities"] if previous else None)
    matches = match_lines(geometry["lines"], utilities)
    statuses = Counter(result["status"] for result in utilities.values())
    records = {r["id"]: r for result in utilities.values() for r in result["records"]}
    confirmed = [m for m in matches.values() if m["method"] == "operator_name_voltage"]
    stats = {
        "line_count": len(geometry["lines"]), "record_count": len(records),
        "matched_line_count": len(confirmed), "candidate_line_count": len(matches) - len(confirmed),
        "capacity_line_count": sum(records[m["records"][0]]["available_mw"] is not None for m in confirmed),
        "utilities_ok": statuses["ok"], "utilities_stale": statuses["stale"], "utilities_failed": statuses["failed"],
    }
    snapshot = {
        "schema_version": 1, "week": week_id, "created_at": now.isoformat(),
        "geometry": write_geometry(history, geometry), "stats": stats,
        "utilities": utilities, "matches": matches,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    # Write completely, then rename; never expose a partial JSON or overwrite a week.
    temporary = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(encode(snapshot))
    try:
        # Hard-link creation is atomic and fails if another publisher already
        # created this week (unlike rename(), which replaces files on POSIX).
        os.link(temporary, target)
    finally:
        temporary.unlink()
    rebuild_index(history)
    LOG.info("Created %s: %s", week_id, stats)
    return snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, default=HISTORY)
    parser.add_argument("--refresh-geometry", action="store_true", help="Fetch nationwide OSM geometry instead of reusing the last snapshot")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    create_snapshot(args.history, refresh_geometry=args.refresh_geometry)


if __name__ == "__main__":
    main()
