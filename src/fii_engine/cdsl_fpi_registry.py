"""Collect and normalize CDSL's official alphabetic registered-FPI directory."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from http.cookiejar import CookieJar
from zoneinfo import ZoneInfo

LETTERS = tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
REGISTRY_URL = "https://www.cdslindia.com/Publications/RegForeignInstInvstr.aspx?indexname={letter}"
PARSER_VERSION = 1
MIN_EXPECTED_RECORDS = 10_000
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
CSV_FIELDS = [
    "registered_name", "normalized_name", "registration_no",
    "valid_upto_raw", "valid_upto_date", "country", "address",
    "source_letter", "source_url", "source_page_sha256", "retrieved_at_utc",
]


class _RegistryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value and self._cell is not None:
            self._cell.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None


def normalize_registry_name(value: str) -> str:
    """Conservative deterministic normalization; never fuzzy-matches names."""
    normalized = value.upper()
    normalized = re.sub(r"\bPLC\b", "PUBLIC LIMITED COMPANY", normalized)
    normalized = re.sub(r"\bLTD\b", "LIMITED", normalized)
    normalized = re.sub(r"\bPVT\b", "PRIVATE", normalized)
    normalized = re.sub(r"[^A-Z0-9]+", " ", normalized)
    return " ".join(normalized.split())


def parse_valid_upto(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    for fmt in ("%d-%m-%Y %H:%M:%S", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_registry_html_with_stats(payload: bytes) -> dict:
    parser = _RegistryParser()
    parser.feed(payload.decode("utf-8", errors="replace"))
    records = []
    for table in parser.tables:
        fields = {}
        for row in table:
            if len(row) < 2:
                continue
            label = row[0].strip().lower().rstrip(":")
            value = row[1].strip()
            if label.startswith("country name"):
                fields.setdefault("country", value)
            elif label.endswith("name") and "registration" not in label:
                fields.setdefault("registered_name", value)
            elif label.startswith("registration no"):
                fields.setdefault("registration_no", value)
            elif label.startswith("registration valid upto"):
                fields.setdefault("valid_upto_raw", value)
            elif label == "address":
                fields.setdefault("address", value)
        if fields.get("registered_name") and fields.get("registration_no"):
            records.append({
                "registered_name": fields["registered_name"],
                "registration_no": fields["registration_no"],
                "valid_upto_raw": fields.get("valid_upto_raw", ""),
                "country": fields.get("country", ""),
                "address": fields.get("address", ""),
            })
    return {
        "records": records,
        "table_count": len(parser.tables),
        "skipped_table_count": len(parser.tables) - len(records),
    }


def parse_registry_html(payload: bytes) -> list[dict[str, str]]:
    return parse_registry_html_with_stats(payload)["records"]


def _fetch(opener, url: str) -> tuple[bytes, str]:
    request = Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
    })
    for attempt in range(4):
        try:
            with opener.open(request, timeout=45) as response:
                if response.status != 200:
                    raise RuntimeError(f"unexpected HTTP {response.status} for {url}")
                payload = response.read()
                if not payload:
                    raise ValueError(f"empty CDSL registry page: {url}")
                return payload, response.headers.get("Content-Type", "")
        except HTTPError as exc:
            if exc.code in {403, 429, 500, 502, 503, 504} and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"CDSL registry request failed: HTTP {exc.code} {url}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"CDSL registry request failed: {exc} {url}") from exc
    raise RuntimeError(f"CDSL registry request failed after retries: {url}")


def _registry_metrics(records: list[dict]) -> dict:
    registration_names: dict[str, set[str]] = defaultdict(set)
    normalized_registrations: dict[str, set[str]] = defaultdict(set)
    registration_counts = Counter()
    name_counts = Counter()
    validity_parse_errors = 0
    for row in records:
        registration = row["registration_no"]
        name = row["normalized_name"]
        registration_names[registration].add(name)
        normalized_registrations[name].add(registration)
        registration_counts[registration] += 1
        name_counts[name] += 1
        if row["valid_upto_raw"] and row["valid_upto_date"] is None:
            validity_parse_errors += 1
    return {
        "record_count": len(records),
        "unique_registration_number_count": len(registration_counts),
        "unique_normalized_name_count": len(name_counts),
        "duplicate_registration_number_excess": sum(
            count - 1 for count in registration_counts.values() if count > 1
        ),
        "duplicate_normalized_name_excess": sum(
            count - 1 for count in name_counts.values() if count > 1
        ),
        "registration_numbers_with_multiple_names": sum(
            len(names) > 1 for names in registration_names.values()
        ),
        "normalized_names_with_multiple_registration_numbers": sum(
            len(registrations) > 1 for registrations in normalized_registrations.values()
        ),
        "validity_date_parse_error_count": validity_parse_errors,
    }


def _csv_bytes(records: list[dict]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(records)
    return stream.getvalue().encode("utf-8")


def _write_immutable(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise RuntimeError(f"immutable CDSL registry snapshot collision: {path}")
        return
    path.write_bytes(payload)


def collect(
    output_dir: Path = Path("artifacts/cdsl_fpi_registry"),
    data_dir: Path = Path("."),
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    retrieved_at = datetime.now(ZoneInfo("UTC"))
    retrieved_at_utc = retrieved_at.isoformat()
    retrieved_date_ist = retrieved_at.astimezone(ZoneInfo("Asia/Kolkata")).date().isoformat()
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    all_records = []
    page_results = []

    for letter in LETTERS:
        url = REGISTRY_URL.format(letter=letter)
        item = {"letter": letter, "url": url}
        try:
            payload, content_type = _fetch(opener, url)
            digest = hashlib.sha256(payload).hexdigest()
            raw_path = output_dir / "pages" / f"registry_{letter}.html"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(payload)
            parsed = parse_registry_html_with_stats(payload)
            records = parsed["records"]
            if not records:
                raise ValueError(f"CDSL registry page {letter} contained no parsed records")
            for record in records:
                valid_date = parse_valid_upto(record["valid_upto_raw"])
                all_records.append({
                    **record,
                    "normalized_name": normalize_registry_name(record["registered_name"]),
                    "valid_upto_date": valid_date or "",
                    "source_letter": letter,
                    "source_url": url,
                    "source_page_sha256": digest,
                    "retrieved_at_utc": retrieved_at_utc,
                })
            item.update({
                "status": "success",
                "http_status": 200,
                "content_type": content_type,
                "bytes": len(payload),
                "sha256": digest,
                "raw_path": str(raw_path.relative_to(output_dir)),
                "table_count": parsed["table_count"],
                "skipped_table_count": parsed["skipped_table_count"],
                "record_count": len(records),
            })
        except Exception as exc:
            item.update({"status": "unavailable", "error": str(exc)})
        page_results.append(item)
        time.sleep(0.25)

    failed = [row for row in page_results if row["status"] != "success"]
    metrics = _registry_metrics(all_records)
    status = (
        "complete"
        if not failed and metrics["record_count"] >= MIN_EXPECTED_RECORDS
        else "partial"
    )
    manifest = {
        "dataset": "cdsl_registered_fpi_directory",
        "publisher": "Central Depository Services (India) Limited",
        "parser_version": PARSER_VERSION,
        "retrieved_at_utc": retrieved_at_utc,
        "retrieved_date_ist": retrieved_date_ist,
        "status": status,
        "expected_letter_pages": len(LETTERS),
        "successful_letter_pages": len(page_results) - len(failed),
        "failed_letter_pages": failed,
        "minimum_expected_record_count": MIN_EXPECTED_RECORDS,
        **metrics,
        "pages": page_results,
        "temporal_scope": (
            "Current directory snapshot with validity end dates only. Registration start dates "
            "are not supplied; historical active status before the retrieval date must remain unknown."
        ),
    }
    artifact_manifest = output_dir / "manifest.json"
    artifact_manifest.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if status != "complete":
        raise RuntimeError(
            f"CDSL FPI registry incomplete: {len(failed)} failed pages; "
            f"{metrics['record_count']} records"
        )

    all_records.sort(key=lambda row: (
        row["normalized_name"], row["registration_no"], row["source_letter"]
    ))
    csv_payload = _csv_bytes(all_records)
    snapshot_sha = hashlib.sha256(csv_payload).hexdigest()
    snapshot_stem = (
        f"cdsl_fpi_registry_{retrieved_date_ist.replace('-', '')}_{snapshot_sha[:16]}"
    )
    snapshot_csv_rel = f"data/snapshots/cdsl_fpi_registry/{snapshot_stem}.csv"
    snapshot_json_rel = f"data/snapshots/cdsl_fpi_registry/{snapshot_stem}.json"
    current_csv_rel = "data/raw/cdsl_fpi_registry/cdsl_fpi_registry_current.csv"
    current_manifest_rel = "data/raw/cdsl_fpi_registry/cdsl_fpi_registry_current_manifest.json"
    snapshot_manifest = {
        **manifest,
        "normalized_csv_sha256": snapshot_sha,
        "normalized_csv_path": snapshot_csv_rel,
        "current_csv_path": current_csv_rel,
        "snapshot_manifest_path": snapshot_json_rel,
        "record_policy": (
            "all parsed source records retained; duplicate names and registration numbers are not discarded"
        ),
    }
    root = data_dir
    _write_immutable(root / snapshot_csv_rel, csv_payload)
    _write_immutable(
        root / snapshot_json_rel,
        (json.dumps(snapshot_manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
    )
    current_csv = root / current_csv_rel
    current_csv.parent.mkdir(parents=True, exist_ok=True)
    current_csv.write_bytes(csv_payload)
    current_manifest = root / current_manifest_rel
    current_manifest.parent.mkdir(parents=True, exist_ok=True)
    current_manifest.write_text(
        json.dumps(snapshot_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    manifest.update({
        "normalized_csv_sha256": snapshot_sha,
        "normalized_csv_path": snapshot_csv_rel,
        "current_manifest_path": current_manifest_rel,
        "snapshot_manifest_path": snapshot_json_rel,
    })
    artifact_manifest.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return snapshot_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/cdsl_fpi_registry"))
    parser.add_argument("--data-dir", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(collect(args.output_dir, args.data_dir), indent=2))


if __name__ == "__main__":
    main()
