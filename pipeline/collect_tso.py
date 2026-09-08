"""Download and parse public TSO CSVs, retaining provenance and missing values.

No login, browser automation, inferred capacity or demonstration data is used.
An incomplete utility download fails as a unit; snapshots can retain its last
successful collection with an explicit stale flag.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import re
import unicodedata
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from pipeline.tso_sources import SOURCES

LOG = logging.getLogger(__name__)
MAX_DOWNLOAD = 20 * 1024 * 1024
MAX_EXPANDED = 40 * 1024 * 1024


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value)))


def decode(data):
    for encoding in ("utf-8-sig", "cp932", "euc_jp"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError("Unrecognized text encoding")


class PublicLinks(HTMLParser):
    def __init__(self, url):
        super().__init__()
        self.url, self.links, self.current = url, [], None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("a", "area") and attrs.get("href"):
            link = {"url": urljoin(self.url, attrs["href"]), "text": attrs.get("alt", "")}
            self.links.append(link)
            if tag == "a":
                self.current = link

    def handle_data(self, data):
        if self.current is not None:
            self.current["text"] += data

    def handle_endtag(self, tag):
        if tag == "a":
            self.current = None


class Downloader:
    def __init__(self, blob_dir: Path):
        self.blob_dir = blob_dir
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "Grid-Mapper/2.0 (weekly public grid-data collection; https://github.com/ANE-Collaboration/Grid-Mapper)"
        self.session.mount("https://", HTTPAdapter(max_retries=Retry(
            total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"], respect_retry_after_header=True)))

    def get(self, url):
        with self.session.get(url, timeout=(15, 60), stream=True) as response:
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_content(64 * 1024):
                size += len(chunk)
                if size > MAX_DOWNLOAD:
                    raise ValueError(f"Download exceeds {MAX_DOWNLOAD} bytes: {url}")
                chunks.append(chunk)
            data = b"".join(chunks)
            digest = hashlib.sha256(data).hexdigest()
            # Content addressed source files are shared between weeks.
            suffix = ".zip" if data.startswith(b"PK") else ".csv" if urlparse(url).path.endswith(".csv") else ".bin"
            path = self.blob_dir / (digest + suffix)
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            return data, {
                "url": url, "resolved_url": response.url, "sha256": digest,
                "bytes": size, "archive": f"sources/{path.name}",
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "http_last_modified": response.headers.get("Last-Modified"),
            }


def discover(key, data):
    source = SOURCES[key]
    if key == "chubu":
        manifest = json.loads(decode(data))
        urls = [urljoin(source["url"], value) for name, value in manifest.items()
                if name.startswith("unyoyoryoto") and "CsvFileName" in name]
    else:
        parser = PublicLinks(source["url"])
        parser.feed(decode(data))
        urls = [link["url"] for link in parser.links
                if re.search(source["pattern"], urlparse(link["url"]).path, re.I)
                and (not source.get("text_pattern") or source["text_pattern"] in compact(link["text"]))]
    urls = sorted(set(urls))
    if len(urls) < source["minimum_files"]:
        raise ValueError(f"Found {len(urls)} downloads; expected at least {source['minimum_files']}. Page layout may have changed")
    host = urlparse(source["url"]).hostname
    if any(urlparse(url).scheme != "https" or urlparse(url).hostname != host for url in urls):
        raise ValueError("Discovered download outside the official source host")
    return urls


def csv_members(data, filename):
    if not data.startswith(b"PK"):
        yield filename, data
        return
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        files = archive.infolist()
        if len(files) > 500 or sum(item.file_size for item in files) > MAX_EXPANDED:
            raise ValueError("Archive exceeds extraction limits")
        for item in files:
            if item.filename.lower().endswith(".csv") and not item.is_dir():
                name = item.filename
                if not item.flag_bits & 0x800:
                    try:
                        name = name.encode("cp437").decode("cp932")
                    except (UnicodeEncodeError, UnicodeDecodeError):
                        pass
                # Read in memory; never use untrusted archive paths on disk.
                yield name, archive.read(item)


def number(value, signed=False):
    text = compact(value).replace(",", "").replace("−", "-")
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        return None
    result = float(text)
    return result if math.isfinite(result) and (signed or result >= 0) else None


def parse_csv(data, utility, document_id, member):
    rows = list(csv.reader(io.StringIO(decode(data))))
    header_index = next((i for i, row in enumerate(rows[:20])
                         if any(compact(c) in ("送電線名", "線路名") for c in row)), None)
    if header_index is None:
        # Known transformer/fence tables can accompany line tables in ZIPs.
        if any(any(compact(c) in ("変電所名", "変圧器名", "フェンス名") for c in row) for row in rows[:20]):
            return [], None
        raise ValueError(f"No recognized line-table header: {member}")
    header = [compact(c) for c in rows[header_index]]
    width = len(header)
    name_col = next(i for i, c in enumerate(header) if c in ("送電線名", "線路名"))
    voltage_col = next((i for i, c in enumerate(header) if c.startswith("電圧")), None)
    if voltage_col is None:
        raise ValueError(f"Missing voltage column: {member}")
    start = header_index + 1
    if utility == "tepco":
        # TEPCO uses five stacked header rows and three flow-direction columns.
        if width not in (19, 21) or name_col != 1 or voltage_col != 2:
            raise ValueError(f"TEPCO table layout changed: {member}")
        offset = width - 19  # Chiba also publishes pre-control capacity and OLR.
        if header[5 + offset] != "運用" or header[10 + offset] != "予想" or header[-1] != "備考":
            raise ValueError(f"TEPCO table headers changed: {member}")
        combined = ["".join(compact(rows[i][j]) if j < len(rows[i]) else ""
                            for i in range(header_index, min(header_index + 5, len(rows)))) for j in range(width)]
        if "当該設備" not in combined[11 + offset] or "上位系" not in combined[12 + offset]:
            raise ValueError("TEPCO capacity-column layout changed")
        header = combined
        header[12 + offset] = "空容量上位系等考慮(MW)"
        if offset:
            header[5] = "電制適用前容量(MW)"
        start = header_index + 5

    def col(predicate):
        return next((i for i, value in enumerate(header) if predicate(value)), None)

    columns = {
        "operational_mw": col(lambda h: "運用容量値" in h),
        "forecast_mw": col(lambda h: "予想潮流" in h),
        "asset_headroom_mw": col(lambda h: "空容量" in h and "上位" not in h),
        "upstream_headroom_mw": col(lambda h: "空容量" in h and "上位" in h),
        "n1_mw": col(lambda h: "N-1電制適用可能量" in h),
        "n1_status": col(lambda h: "N-1電制適用可否" in h),
        "curtailment": col(lambda h: h.startswith("平常時") and (h.endswith("可能性") or h.endswith("制御可能性"))),
        "notes": col(lambda h: h == "備考"),
    }
    if all(columns[k] is None for k in ("operational_mw", "asset_headroom_mw", "n1_status", "curtailment")):
        raise ValueError(f"No recognized grid data columns: {member}")
    label = " ".join(c.strip() for row in rows[:header_index] for c in row if c.strip())[:400]
    date_match = re.search(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日", label)
    published_date = "-".join([date_match[1], date_match[2].zfill(2), date_match[3].zfill(2)]) if date_match else None
    records = []
    for row_number, row in enumerate(rows[start:], start + 1):
        if len(row) <= voltage_col or number(row[voltage_col]) is None:
            continue
        voltage = number(row[voltage_col])
        if not 0 < voltage <= 1000:
            raise ValueError(f"Invalid voltage in {member} row {row_number}")
        row = row + [""] * max(0, width - len(row))
        record = {
            "id": hashlib.sha256(f"{document_id}\n{member}\n{row_number}".encode()).hexdigest()[:20],
            "utility": utility, "document": document_id, "member": member, "row": row_number,
            "asset_id": row[0].strip().lstrip("'"), "name": row[name_col].strip(), "voltage_kv": voltage,
            "published_date": published_date,
        }
        for field, index in columns.items():
            value = row[index].strip() if index is not None else ""
            record[field] = number(value, signed=field == "forecast_mw") if field.endswith("_mw") else value or None
        # Prefer upstream-constrained published capacity. A missing upstream
        # value never falls back to a more permissive equipment-only value.
        record["capacity_scope"] = "upstream" if columns["upstream_headroom_mw"] is not None else "asset"
        record["available_mw"] = record["upstream_headroom_mw"] if record["capacity_scope"] == "upstream" else record["asset_headroom_mw"]
        record["capacity_status"] = ("CAPACITY_UNDISCLOSED" if record["available_mw"] is None
                                     else "AVAILABLE" if record["available_mw"] > 0 else "NO_PUBLISHED_HEADROOM")
        records.append(record)
    if not records:
        raise ValueError(f"Recognized table contains no valid line records: {member}")
    return records, {"member": member, "published_date": published_date, "published_label": label, "record_count": len(records)}


def collect_utility(key, downloader):
    source = SOURCES[key]
    discovery_url = urljoin(source["url"], "pass_data/pass.json") if key == "chubu" else source["url"]
    page, page_info = downloader.get(discovery_url)
    urls = discover(key, page)
    documents, records = {}, []
    for url in urls:
        data, info = downloader.get(url)
        document_id = hashlib.sha256(url.encode()).hexdigest()[:16]
        tables, file_records = [], []
        for member, content in csv_members(data, urlparse(url).path.rsplit("/", 1)[-1]):
            parsed, table = parse_csv(content, key, document_id, member)
            file_records.extend(parsed)
            if table:
                tables.append(table)
        if not file_records:
            raise ValueError(f"Download contains no valid transmission line table: {url}")
        documents[document_id] = {**info, "tables": tables}
        records.extend(file_records)
    return {
        "utility": key, "name": source["name"], "landing_url": source["url"],
        "status": "ok", "last_success_at": datetime.now(timezone.utc).isoformat(),
        "discovery": page_info, "documents": documents, "records": records,
    }


def collect_all(blob_dir, previous=None, downloader=None):
    downloader = downloader or Downloader(Path(blob_dir))
    previous = previous or {}
    results = {}
    for key, source in SOURCES.items():
        try:
            result = collect_utility(key, downloader)
            old_count = len(previous.get(key, {}).get("records", []))
            if old_count and len(result["records"]) < old_count * 0.7:
                raise ValueError(f"Record count fell by more than 30% (previously {old_count}); review source changes")
            results[key] = result
            LOG.info("%s: %d records from %d downloads", key, len(result["records"]), len(result["documents"]))
        except Exception as exc:
            LOG.error("%s collection failed: %s", key, exc)
            old = previous.get(key)
            results[key] = {
                **(old or {"utility": key, "name": source["name"], "landing_url": source["url"], "records": [], "documents": {}}),
                "status": "stale" if old and old.get("records") else "failed",
                "error": str(exc), "attempted_at": datetime.now(timezone.utc).isoformat(),
            }
    if not any(value["status"] == "ok" for value in results.values()):
        raise RuntimeError("Every TSO collection failed; no new snapshot will be published")
    return results
