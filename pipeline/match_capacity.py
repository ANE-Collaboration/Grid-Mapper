"""Conservative links between OSM ways and published utility line records."""
import re
from collections import defaultdict

from pipeline.collect_tso import compact

ALIASES = {
    "hokkaido": ("北海道電力", "hokkaidoelectric", "hepco"),
    "tohoku": ("東北電力", "tohokuelectric"),
    "tepco": ("東京電力", "tepco", "tokyoelectric"),
    "chubu": ("中部電力", "chubuelectric", "chuden"),
    "hokuriku": ("北陸電力", "hokurikuelectric", "rikuden"),
    "kansai": ("関西電力", "kansaielectric", "kepco"),
    "chugoku": ("中国電力", "chugokuelectric", "energia"),
    "shikoku": ("四国電力", "shikokuelectric", "yonden"),
    "kyushu": ("九州電力", "kyushuelectric", "kyuden"),
    "okinawa": ("沖縄電力", "okinawaelectric", "okiden"),
}
# Used only to reject implausible candidates when OSM has no operator tag.
BOUNDS = {
    "hokkaido": (139, 41, 146.5, 46), "tohoku": (137, 36.4, 142.5, 41.7),
    "tepco": (137.5, 34, 141.6, 38), "chubu": (135.5, 33.5, 139, 37.2),
    "hokuriku": (135, 35.3, 138.8, 38), "kansai": (133.5, 33.3, 137.3, 36.5),
    "chugoku": (130.5, 33.5, 134.8, 36.5), "shikoku": (132, 32.5, 135, 34.7),
    "kyushu": (128, 27, 132.5, 34.8), "okinawa": (122, 24, 132, 28.5),
}


def utility_for(operator):
    text = compact(operator).lower()
    matches = [key for key, values in ALIASES.items() if any(alias in text for alias in values)]
    return matches[0] if len(matches) == 1 else None


def line_key(name):
    # Keep section names, brackets and circuit numbers: they may identify
    # electrically different assets. Only the optional final 線 is equivalent.
    name = compact(name).lower()
    return name[:-1] if name.endswith("線") else name


def match_lines(lines, utilities):
    index = defaultdict(list)
    for utility, result in utilities.items():
        for record in result["records"]:
            name = line_key(record["name"])
            if name and not re.fullmatch(r"[-ー―\d.]+|他社線?|非公開|不明", name):
                index[(name, record["voltage_kv"])].append((utility, record["id"]))
    matches = {}
    for identifier, name, operator, voltage, _power, coordinates in lines:
        utility = utility_for(operator)
        if operator and utility is None:
            continue  # Railways, J-Power and other owners are separate networks.
        names = [part for part in re.split(r"[;；]", name) if part.strip()]
        candidates = []
        for part in names:
            found = index.get((line_key(part), voltage), [])
            if utility:
                found = [item for item in found if item[0] == utility]
            else:
                x, y = coordinates[len(coordinates) // 2][:2]
                found = [item for item in found if BOUNDS[item[0]][0] <= x <= BOUNDS[item[0]][2]
                         and BOUNDS[item[0]][1] <= y <= BOUNDS[item[0]][3]]
            candidates.extend(found)
        candidates = sorted(set(candidates))
        if not candidates:
            continue
        # With no operator, publish a candidate for inspection, not a capacity
        # classification. Name + regional proximity cannot prove ownership.
        confirmed = len(candidates) == 1 and len(names) == 1 and utility is not None
        matches[str(identifier)] = {
            "records": [record_id for _, record_id in candidates],
            "method": "operator_name_voltage" if confirmed else "candidate",
        }
    return matches
