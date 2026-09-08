"""
pipeline/extract_osm.py
Japan Power Line Extractor (Overpass API and explicit offline test mode).

Extracts mapped lines, minor lines and cables at every voltage within Japan
(ISO3166-1=JP) into RFC 7946 GeoJSON. Live failures never substitute fixtures.

Author: Japan Grid Mapper ETL Pipeline Team
Integrity Mode: Development / Zero-Facade
"""

import argparse
import json
import logging
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

# Configure module logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("extract_osm")

# Default Overpass API settings
DEFAULT_OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"
DEFAULT_USER_AGENT = "GridMapper-ETL/1.0 (+https://github.com/curci/Grid-Mapper)"
DEFAULT_TIMEOUT_SEC = 300

# Default project paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_GEOJSON = PROJECT_ROOT / "data" / "raw_osm_lines.geojson"
DEFAULT_FIXTURE_GEOJSON = PROJECT_ROOT / "tests" / "fixtures" / "sample_osm_lines.geojson"

# Query physical ways once, including untagged voltages and distribution lines.
# Inline geometry avoids duplicate skeleton ways overwriting their original tags.
OVERPASS_QUERY_JAPAN = """
[out:json][timeout:300];
area["ISO3166-1"="JP"][admin_level=2]->.japan;
way["power"~"^(line|minor_line|cable)$"](area.japan);
out body geom qt;
"""

# Defensive normalizer imports with inline fallbacks
try:
    from pipeline.normalize import clean_voltage, normalize_line_name
except ImportError:
    try:
        from normalize import clean_voltage, normalize_line_name
    except ImportError:
        import re
        import unicodedata

        def clean_voltage(voltage_str: Any) -> int:
            if not voltage_str or isinstance(voltage_str, (list, dict, set, tuple)):
                return 0
            norm = unicodedata.normalize("NFKC", str(voltage_str))
            digits = re.findall(r"\d+", norm)
            if not digits:
                return 0
            val = int(digits[0])
            if val >= 10000:
                val = int(val / 1000)
            return val

        def normalize_line_name(name: Any) -> str:
            if not name or not isinstance(name, str):
                return ""
            s = unicodedata.normalize("NFKC", name)
            s = re.sub(r"\[.*?\]|\(.*?\)|【.*?】|（.*?）|〈.*?〉|《.*?》", "", s)
            s = re.sub(r"^\d+kV|\d+kV$", "", s)
            s = re.sub(r"[0-9]+[・/,\-][0-9]+号(線)?", "", s)
            s = re.sub(r"[0-9]+号(線)?", "", s)
            s = re.sub(r"No\.\s*[0-9]+", "", s, flags=re.IGNORECASE)
            s = re.sub(r"[0-9]+L$", "", s)
            s = re.sub(r"[甲乙]$", "", s)
            s = s.replace("送電線", "").replace("線路", "").replace("線", "")
            s = re.sub(r"[\s\-_　]", "", s)
            return s.strip()


def build_http_session(retries: int = 3, backoff_factor: float = 2.0) -> requests.Session:
    """Configures a requests Session with automated exponential retries."""
    session = requests.Session()
    retry_strategy = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST", "GET"],
        raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def query_overpass(
    query: str,
    endpoint: str = DEFAULT_OVERPASS_ENDPOINT,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    user_agent: str = DEFAULT_USER_AGENT
) -> Dict[str, Any]:
    """
    Executes a POST request to the Overpass API interpreter.
    Raises requests.RequestException or ValueError on failure.
    """
    session = build_http_session()
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    logger.info("Dispatching Overpass QL query to: %s (timeout: %ds)", endpoint, timeout)
    resp = session.post(endpoint, data={"data": query}, headers=headers, timeout=(30, timeout + 30))
    resp.raise_for_status()

    data = resp.json()
    if data.get("remark"):
        raise ValueError(f"Overpass API returned an incomplete response: {data['remark']}")
    if not isinstance(data.get("elements"), list):
        raise ValueError("Overpass response is missing its elements list")
    return data


def parse_overpass_json(raw_json: Dict[str, Any]) -> Dict[str, Any]:
    """
    Converts raw Overpass JSON elements (nodes, ways, relations) into
    RFC 7946 GeoJSON FeatureCollection with LineString and MultiLineString geometries.
    """
    elements = raw_json.get("elements") or []
    logger.info("Parsing %d Overpass elements...", len(elements))

    # Phase 1: Node coordinate caching [lon, lat]
    nodes: Dict[int, List[float]] = {}
    for el in elements:
        if el.get("type") == "node" and "id" in el and "lon" in el and "lat" in el:
            nodes[el["id"]] = [float(el["lon"]), float(el["lat"])]

    # Phase 2: Way coordinate caching and filtering
    ways_cache: Dict[int, Dict[str, Any]] = {}
    for el in elements:
        if el.get("type") == "way" and "id" in el:
            way_id = el["id"]
            node_ids = el.get("nodes") or []
            inline = el.get("geometry")
            if inline is not None:
                if any(not pt or "lon" not in pt or "lat" not in pt for pt in inline):
                    raise ValueError(f"Incomplete geometry for OSM way {way_id}")
                coords = [[float(pt["lon"]), float(pt["lat"])] for pt in inline]
            else:
                if sum(nid in nodes for nid in node_ids) < 2:
                    continue
                if any(nid not in nodes for nid in node_ids):
                    raise ValueError(f"Missing nodes for OSM way {way_id}; refusing to bridge a geometry gap")
                coords = [nodes[nid] for nid in node_ids]
            if len(coords) >= 2:
                previous_tags = ways_cache.get(way_id, {}).get("tags", {})
                ways_cache[way_id] = {
                    "coords": coords,
                    "tags": {**previous_tags, **(el.get("tags") or {})},
                    "id": way_id
                }

    features: List[Dict[str, Any]] = []
    used_way_ids: set = set()

    # Phase 3a: Relation (Power Route) Assembly -> MultiLineString / LineString
    for el in elements:
        if el.get("type") == "relation" and "id" in el:
            tags = el.get("tags") or {}
            if tags.get("route") == "power" or tags.get("power") in ("line", "cable"):
                member_coords = []
                for m in (el.get("members") or []):
                    if m.get("type") == "way" and m.get("ref") in ways_cache:
                        ref_id = m["ref"]
                        w_coords = ways_cache[ref_id]["coords"]
                        member_coords.append(w_coords)
                        used_way_ids.add(ref_id)

                if member_coords:
                    if len(member_coords) == 1:
                        geom = {"type": "LineString", "coordinates": member_coords[0]}
                    else:
                        geom = {"type": "MultiLineString", "coordinates": member_coords}

                    props = _extract_properties(el["id"], "relation", tags)
                    features.append({
                        "type": "Feature",
                        "geometry": geom,
                        "properties": props
                    })

    # Phase 3b: Standalone Ways -> LineString
    for way_id, way_data in ways_cache.items():
        # Avoid duplicate features if way was consumed by relation,
        # unless way has an explicit distinct name
        if way_id in used_way_ids and not way_data["tags"].get("name"):
            continue

        geom = {
            "type": "LineString",
            "coordinates": way_data["coords"]
        }
        props = _extract_properties(way_id, "way", way_data["tags"])
        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": props
        })

    logger.info("Successfully compiled %d GeoJSON features.", len(features))
    return {
        "type": "FeatureCollection",
        "features": features
    }


# Alias for contract compatibility across test suites
parse_osm_elements = parse_overpass_json


def clean_osm_voltage(value: Any) -> Union[int, float]:
    """OSM values are volts unless a kV unit is explicit; keep 6.6 kV precision."""
    raw = unicodedata.normalize("NFKC", str(value or "")).split(";")[0].split("/")[0].strip()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(kV|V)?", raw, flags=re.IGNORECASE)
    if not match:
        return 0
    kv = float(match[1]) / (1 if (match[2] or "").lower() == "kv" else 1000)
    # Retain malformed source tags in voltage_raw, never display absurd voltages.
    if not 0 < kv <= 1000:
        return 0
    return int(kv) if kv.is_integer() else kv


def _extract_properties(osm_id: int, osm_type: str, tags: Dict[str, Any]) -> Dict[str, Any]:
    """Constructs the unified property schema dictionary for a feature."""
    tags = tags or {}
    name = tags.get("name", "")
    name_en = tags.get("name:en", "")
    operator = tags.get("operator", "")
    voltage_raw = str(tags.get("voltage", ""))
    circuits = str(tags.get("circuits", "1"))
    power_type = tags.get("power", "line")

    return {
        "osm_id": osm_id,
        "osm_type": osm_type,
        "name": name,
        "name_en": name_en,
        "norm_name": normalize_line_name(name),
        "operator": operator,
        "voltage_raw": voltage_raw,
        "voltage_kv": clean_osm_voltage(voltage_raw),
        "circuits": circuits,
        "power": power_type
    }


def load_fixture_geojson(fixture_path: Union[str, Path] = DEFAULT_FIXTURE_GEOJSON) -> Dict[str, Any]:
    """Loads and validates the offline GeoJSON fixture."""
    path = Path(fixture_path)
    if not path.exists():
        raise FileNotFoundError(f"Offline fixture file not found at: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("type") != "FeatureCollection":
        raise ValueError(f"Invalid GeoJSON fixture: expected FeatureCollection, got {data.get('type')}")
    logger.info("Loaded %d features from offline fixture: %s", len(data.get("features") or []), path)
    return data


# Alias for contract compatibility across test suites
load_offline_fixture = load_fixture_geojson


def fetch_osm_power_lines(
    output_path: Optional[Union[str, Path]] = DEFAULT_OUTPUT_GEOJSON,
    offline: bool = False,
    fixture_path: Union[str, Path] = DEFAULT_FIXTURE_GEOJSON,
    endpoint: str = DEFAULT_OVERPASS_ENDPOINT,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    # Compatibility arguments
    output_geojson: Optional[Union[str, Path]] = None,
    use_offline: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Primary ETL extraction entry point.
    - If offline=True or USE_CACHED_OSM=1, loads directly from fixture_path.
    - If offline=False, attempts live Overpass API query with retries.
    - Live failures raise and leave the previous output intact. Never publish fixtures implicitly.
    - Writes resulting FeatureCollection to output_path if provided.
    """
    if output_geojson is not None:
        output_path = output_geojson
    if use_offline is not None:
        offline = use_offline

    env_offline = os.getenv("USE_CACHED_OSM", "").lower() in ("1", "true", "yes") or \
                  os.getenv("OSM_OFFLINE", "").lower() in ("1", "true", "yes")

    feature_collection: Optional[Dict[str, Any]] = None

    if offline or env_offline:
        logger.info("Offline mode requested. Loading fixture from: %s", fixture_path)
        feature_collection = load_fixture_geojson(fixture_path)
        feature_collection["metadata"] = {"source": "test-fixture", "is_demo": True}
    else:
        logger.info("Attempting live nationwide Overpass API query...")
        query = OVERPASS_QUERY_JAPAN.replace("[timeout:300]", f"[timeout:{timeout}]")
        raw_data = query_overpass(query, endpoint=endpoint, timeout=timeout)
        feature_collection = parse_overpass_json(raw_data)
        if not feature_collection["features"]:
            raise ValueError("Live extraction returned no power lines; previous data has been retained")
        feature_collection["metadata"] = {
            "source": "OpenStreetMap / Overpass",
            "source_url": endpoint,
            "is_demo": False,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "osm_timestamp": raw_data.get("osm3s", {}).get("timestamp_osm_base"),
            "scope": "Japan: power=line, minor_line, cable; all mapped voltages",
        }

    if output_path is not None and feature_collection is not None:
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        temp_path = out_p.with_suffix(out_p.suffix + ".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(feature_collection, f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        temp_path.replace(out_p)
        logger.info("Wrote %d features to output file: %s", len(feature_collection.get("features", [])), out_p)

    return feature_collection


def main():
    parser = argparse.ArgumentParser(description="Extract Japan Extra-High Voltage Power Lines into GeoJSON")
    parser.add_argument("--output", "-o", default=str(DEFAULT_OUTPUT_GEOJSON), help="Output GeoJSON path")
    parser.add_argument("--offline", action="store_true", help="Force offline mode using fixture")
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE_GEOJSON), help="Path to offline GeoJSON fixture")
    parser.add_argument("--endpoint", default=DEFAULT_OVERPASS_ENDPOINT, help="Overpass API endpoint")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC, help="Query timeout in seconds")

    args = parser.parse_args()
    try:
        fetch_osm_power_lines(
            output_path=args.output,
            offline=args.offline,
            fixture_path=args.fixture,
            endpoint=args.endpoint,
            timeout=args.timeout
        )
        print(f"Extraction completed successfully. Output: {args.output}")
    except Exception as e:
        logger.error("Extraction process failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
