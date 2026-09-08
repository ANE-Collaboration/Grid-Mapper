"""Version 2 regression tests: real schemas, conservative matching and history."""
import copy
import csv
import gzip
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipeline import collect_tso as collector
from pipeline.collect_tso import parse_csv, discover, csv_members, number
from pipeline.match_capacity import match_lines
from pipeline.weekly_snapshot import create_snapshot, pack_geometry, rebuild_index

FIXTURES = Path(__file__).parent / "fixtures" / "public_tso"
SAMPLES = json.loads((FIXTURES / "sources.json").read_bytes())


@pytest.mark.parametrize("sample", SAMPLES, ids=[s["file"] for s in SAMPLES])
def test_real_public_headers(sample):
    records, table = parse_csv((FIXTURES / sample["file"]).read_bytes(), sample["utility"], "doc", sample["member"])
    assert len(records) == 4
    assert all(record["utility"] == sample["utility"] and record["voltage_kv"] > 0 for record in records)
    assert table["record_count"] == 4
    assert all(record["available_mw"] is None for record in records)


def test_tepco_chiba_uses_operating_limit_after_control():
    records, _ = parse_csv((FIXTURES / "tepco-5.csv").read_bytes(), "tepco", "doc", "chiba.csv")
    assert records[0]["name"] == "東葛線1・2L"
    assert records[0]["operational_mw"] == 290
    assert records[0]["forecast_mw"] == 62
    assert records[0]["n1_mw"] == 222


@pytest.mark.parametrize("value", ["", "-", "―", "非公開", "NaN", "Infinity", "-1", "5〜10"])
def test_missing_or_invalid_capacity_is_not_zero(value):
    assert number(value) is None


def test_fractional_zero_and_signed_values():
    assert number("０") == 0
    assert number("６．６") == 6.6
    assert number("-1,234", signed=True) == -1234


def table_bytes(headers, values):
    output = io.StringIO()
    csv.writer(output).writerows([headers, values])
    return output.getvalue().encode("utf-8")


@pytest.mark.parametrize("upstream,expected,status", [("", None, "CAPACITY_UNDISCLOSED"), ("0", 0, "NO_PUBLISHED_HEADROOM"), ("3", 3, "AVAILABLE")])
def test_capacity_is_not_operating_limit_minus_forecast_or_upstream_fallback(upstream, expected, status):
    data = table_bytes(["送電線No", "送電線名", "電圧(kV)", "運用容量値(MW)", "予想潮流(MW)", "空容量(当該設備)(MW)", "空容量(上位系等考慮)(MW)"],
                       ["1", "試験線", "6.6", "100", "10", "50", upstream])
    records, _ = parse_csv(data, "kansai", "doc", "data.csv")
    assert records[0]["available_mw"] == expected
    assert records[0]["capacity_status"] == status
    assert records[0]["asset_headroom_mw"] == 50


def test_absent_capacity_header_is_unknown():
    data = table_bytes(["送電線No", "送電線名", "電圧(kV)", "N-1電制適用可否"], ["1", "試験線", "66", "可"])
    records, _ = parse_csv(data, "okinawa", "doc", "data.csv")
    assert records[0]["available_mw"] is None


def test_changed_header_fails_and_transformer_is_ignored():
    with pytest.raises(ValueError, match="header"):
        parse_csv(b"<html>maintenance</html>", "kansai", "doc", "data.csv")
    assert parse_csv("変電所名,電圧\nA,66".encode(), "kansai", "doc", "data.csv") == ([], None)


def record(identifier="record-a", utility="kansai", name="試験線", capacity=3):
    return {"id": identifier, "utility": utility, "name": name, "voltage_kv": 66,
            "available_mw": capacity, "capacity_status": "AVAILABLE" if capacity else "NO_PUBLISHED_HEADROOM"}


def utility(records=None):
    return {"status": "ok", "last_success_at": "2026-09-01T00:00:00Z", "records": records or [record()], "documents": {}}


def line(operator="関西電力送配電", name="試験線", identifier=1):
    return [identifier, name, operator, 66, "line", [[135, 35], [135.1, 35.1]]]


def test_matching_requires_operator_and_keeps_duplicate_sections_ambiguous():
    lines = [line(), line("九州電力", identifier=2), line("東日本旅客鉄道", identifier=3), line("", identifier=4)]
    utilities = {"kansai": utility(), "kyushu": utility([record("other", "kyushu")])}
    matches = match_lines(lines, utilities)
    assert matches["1"] == {"records": ["record-a"], "method": "operator_name_voltage"}
    assert matches["2"]["records"] == ["other"]
    assert "3" not in matches
    assert matches["4"]["method"] == "candidate"
    utilities["kansai"]["records"].append(record("section-b", capacity=50))
    assert match_lines([line()], utilities)["1"]["method"] == "candidate"


def test_matching_does_not_remove_section_or_circuit_identity():
    utilities = {"kansai": utility()}
    assert not match_lines([line(name="試験線（山側）")], utilities)
    assert match_lines([line(name="試験線;別線")], utilities)["1"]["method"] == "candidate"
    assert not match_lines([line(name="試検線")], utilities)  # No fuzzy match.


def test_area_links_and_minimum_download_guard(monkeypatch):
    monkeypatch.setitem(collector.SOURCES, "tohoku", {**collector.SOURCES["tohoku"], "minimum_files": 1})
    urls = discover("tohoku", b'<map><area href="data/sys_capa_local01_line_202610_02.csv"></map>')
    assert urls[0].endswith("202610_02.csv")
    with pytest.raises(ValueError, match="Found 0"):
        discover("tohoku", b"<html>maintenance</html>")
    with pytest.raises(ValueError, match="official source host"):
        discover("tohoku", b'<area href="https://example.org/sys_capa_local01_line_202610_02.csv">')


def test_archive_paths_are_read_only_in_memory():
    import zipfile
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("../../outside.csv", b"csv data")
    assert list(csv_members(out.getvalue(), "test.zip")) == [("../../outside.csv", b"csv data")]


def test_failed_source_keeps_previous_records_and_date(monkeypatch, tmp_path):
    previous = {"kansai": utility()}
    def collect(key, _):
        if key == "kansai":
            raise ValueError("layout changed")
        return utility([record(key, key)])
    monkeypatch.setattr(collector, "collect_utility", collect)
    results = collector.collect_all(tmp_path, previous, downloader=object())
    assert results["kansai"]["status"] == "stale"
    assert results["kansai"]["records"] == previous["kansai"]["records"]
    assert results["kansai"]["last_success_at"] == previous["kansai"]["last_success_at"]
    assert previous["kansai"]["status"] == "ok"


def test_all_sources_failed_does_not_publish(monkeypatch, tmp_path):
    def fail(*args):
        raise ValueError("offline")
    monkeypatch.setattr(collector, "collect_utility", fail)
    with pytest.raises(RuntimeError, match="Every TSO"):
        collector.collect_all(tmp_path, downloader=object())


def test_history_immutable_idempotent_and_pins_geometry(tmp_path):
    geometry = {"schema_version": 1, "source": {}, "lines": [line()]}
    first = create_snapshot(tmp_path, geometry, now=datetime(2026, 9, 7, tzinfo=timezone.utc), collector=lambda *_: {"kansai": utility()})
    old_bytes = (tmp_path / "snapshots/2026-W37.json").read_bytes()
    def must_not_run(*args):
        raise AssertionError("Existing week must not recollect")
    assert create_snapshot(tmp_path, now=datetime(2026, 9, 9, tzinfo=timezone.utc), collector=must_not_run) == first
    changed = copy.deepcopy(geometry)
    changed["lines"][0][-1][0][0] = 135.05
    second = create_snapshot(tmp_path, changed, now=datetime(2026, 9, 14, tzinfo=timezone.utc), collector=lambda *_: {"kansai": utility([record(capacity=8)])})
    assert first["geometry"] != second["geometry"]
    assert json.loads(gzip.decompress((tmp_path / first["geometry"]).read_bytes())) == geometry
    assert (tmp_path / "snapshots/2026-W37.json").read_bytes() == old_bytes
    assert rebuild_index(tmp_path)["latest"] == "2026-W38"
    with pytest.raises(ValueError, match="backfill"):
        create_snapshot(tmp_path, now=datetime(2026, 8, 1, tzinfo=timezone.utc))


def test_demo_and_truncated_geometry_cannot_be_published():
    with pytest.raises(ValueError, match="verified live"):
        pack_geometry({"metadata": {"is_demo": True}, "features": []})
    with pytest.raises(ValueError, match="1,000"):
        pack_geometry({"metadata": {"source": "OpenStreetMap / Overpass", "is_demo": False}, "features": []})
