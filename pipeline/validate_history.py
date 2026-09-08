"""Validate static history references and archived-source integrity before deployment."""
import argparse
import gzip
import hashlib
import json
import re
from pathlib import Path

from pipeline.weekly_snapshot import HISTORY


def validate_history(root=HISTORY):
    root = Path(root)
    index = json.loads((root / "index.json").read_bytes())
    if index.get("schema_version") != 1 or not index.get("snapshots"):
        raise ValueError("Invalid or empty history index")
    weeks = [item["week"] for item in index["snapshots"]]
    if weeks != sorted(set(weeks), reverse=True) or index["latest"] != weeks[0]:
        raise ValueError("History index is unordered or latest is incorrect")
    files = [p for p in root.rglob("*") if p.is_file()]
    if len(files) > 19000 or sum(p.stat().st_size for p in files) > 900 * 1024 ** 2:
        raise ValueError("History is approaching free static-host limits; migrate storage before publishing")
    if any(p.is_symlink() or p.stat().st_size > 25 * 1024 ** 2 for p in files):
        raise ValueError("Invalid or oversized static asset")
    geometries, checked_sources = {}, set()
    for entry in index["snapshots"]:
        if not re.fullmatch(r"\d{4}-W\d{2}", entry["week"]) or entry["url"] != f"snapshots/{entry['week']}.json":
            raise ValueError("Invalid snapshot path")
        snapshot = json.loads((root / entry["url"]).read_bytes())
        if snapshot["week"] != entry["week"] or snapshot["stats"] != entry["stats"] or snapshot["schema_version"] != 1:
            raise ValueError("Snapshot and index disagree")
        filename = snapshot["geometry"]
        if not re.fullmatch(r"geometry/[a-f0-9]{64}\.json\.gz", filename):
            raise ValueError("Invalid geometry path")
        if filename not in geometries:
            content = gzip.decompress((root / filename).read_bytes())
            if hashlib.sha256(content).hexdigest() != Path(filename).name.split(".")[0]:
                raise ValueError("Geometry checksum mismatch")
            geometry = json.loads(content)
            if geometry["source"].get("is_demo") is not False or geometry["source"].get("source") != "OpenStreetMap / Overpass":
                raise ValueError("Unverified geometry provenance")
            lines = {str(line[0]): line for line in geometry["lines"]}
            if len(lines) != len(geometry["lines"]) or len(lines) < 1000:
                raise ValueError("Duplicate or incomplete nationwide geometry")
            geometries[filename] = lines
        lines = geometries[filename]
        if len(lines) != snapshot["stats"]["line_count"]:
            raise ValueError("Geometry count mismatch")
        records = {}
        for key, utility in snapshot["utilities"].items():
            if utility["status"] not in ("ok", "stale", "failed"):
                raise ValueError("Invalid utility status")
            for document in utility["documents"].values():
                archive = document["archive"]
                if not re.fullmatch(r"sources/[a-f0-9]{64}\.(csv|zip|bin)", archive):
                    raise ValueError("Invalid archive path")
                if archive not in checked_sources:
                    if hashlib.sha256((root / archive).read_bytes()).hexdigest() != document["sha256"]:
                        raise ValueError("Archived source checksum mismatch")
                    checked_sources.add(archive)
            for record in utility["records"]:
                if record["id"] in records or record["utility"] != key or record["document"] not in utility["documents"]:
                    raise ValueError("Invalid record identity or provenance")
                records[record["id"]] = record
        if len(records) != snapshot["stats"]["record_count"]:
            raise ValueError("Record count mismatch")
        for identifier, match in snapshot["matches"].items():
            if identifier not in lines or not match["records"] or any(r not in records for r in match["records"]):
                raise ValueError("Dangling line match")
            if match["method"] == "operator_name_voltage" and len(match["records"]) != 1:
                raise ValueError("Ambiguous record classified as a confirmed match")
    print(f"Validated {len(weeks)} immutable snapshots, {len(geometries)} geometries and {len(checked_sources)} source documents.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, default=HISTORY)
    validate_history(parser.parse_args().history)
