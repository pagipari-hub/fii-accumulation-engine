"""Append-only ingestion of official NSE shareholding XBRL filings."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fii_engine.nse_shareholding_probe import BASE, MASTER_URL, PAGE, _get
from fii_engine.nse_shareholding_xbrl import parse_xbrl

TRUSTED_XBRL_HOST = "nsearchives.nseindia.com"
TRUSTED_XBRL_PATH = "/corporate/xbrl/"
UNIVERSE_PATH = "data/raw/universe/nifty500_current.csv"
PARSER_VERSION = 3

OWNERSHIP_FIELDS = [
    "reporting_date", "symbol", "company_name", "isin",
    "record_id", "submission_date", "broadcast_date", "system_date",
    "revised_data", "revision_date", "identity_status", "identity_join_method",
    "filing_status", "fpi_category_i_shares", "fpi_category_ii_shares",
    "fpi_total_shares", "fpi_category_i_ownership_percentage_points",
    "fpi_category_ii_ownership_percentage_points",
    "fpi_total_ownership_percentage_points",
    "other_foreign_institution_shares", "overseas_depositories_shares",
    "foreign_institutions_total_shares", "fpi_reconciliation_status",
    "xbrl_sha256", "source_master_sha256",
]


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_hash(value: dict) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return _sha256(payload)


def _normal_name(value: object) -> str:
    tokens = re.findall(r"[A-Z0-9]+", str(value or "").upper())
    # Deterministically normalize common legal suffix variants; do not use fuzzy matching.
    suffix_aliases = {
        "LIMITED": "LTD",
        "PRIVATE": "PVT",
        "CORPORATION": "CORP",
        "COMPANY": "CO",
        "INCORPORATED": "INC",
    }
    return "".join(suffix_aliases.get(token, token) for token in tokens)


def _parse_nse_date(value: object, *, field: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"invalid NSE shareholding {field}: {value!r}")


def _parse_nse_datetime(value: object, *, field: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%B-%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    raise ValueError(f"invalid NSE shareholding {field}: {value!r}")


def _load_universe(root: Path) -> tuple[list[dict], dict[str, dict], str]:
    path = root / UNIVERSE_PATH
    payload = path.read_bytes()
    try:
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    except UnicodeDecodeError as exc:
        raise ValueError("Nifty 500 universe CSV is not UTF-8") from exc
    if not reader.fieldnames:
        raise ValueError("Nifty 500 universe CSV has no header")
    fields = {item.strip() for item in reader.fieldnames}
    required = {"symbol", "company_name", "industry", "series", "isin"}
    if not required.issubset(fields):
        raise ValueError(f"Nifty 500 universe schema missing: {sorted(required - fields)}")
    rows = [
        {str(k).strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
        for row in reader
    ]
    if not 480 <= len(rows) <= 520:
        raise ValueError(f"Nifty 500 universe row count outside safety range: {len(rows)}")
    if len({row["symbol"] for row in rows}) != len(rows):
        raise ValueError("Nifty 500 universe has duplicate symbols")
    if len({row["isin"] for row in rows}) != len(rows):
        raise ValueError("Nifty 500 universe has duplicate ISINs")
    return rows, {row["symbol"]: row for row in rows}, _sha256(payload)


def _metadata(master_row: dict, stock: dict) -> dict:
    reporting_date = _parse_nse_date(master_row.get("date"), field="reporting date")
    if reporting_date is None:
        raise ValueError(f"missing reporting date for {master_row.get('symbol')}")
    symbol = str(master_row.get("symbol") or "").strip()
    isin = str(master_row.get("isin") or "").strip() or None
    company_name = str(master_row.get("name") or "").strip()
    if isin:
        if isin == stock["isin"]:
            identity_status, join_method = "verified", "isin_exact"
        else:
            identity_status, join_method = "isin_mismatch", "symbol_only_rejected"
    elif _normal_name(company_name) == _normal_name(stock["company_name"]):
        identity_status, join_method = "verified", "symbol_and_company_name"
    else:
        identity_status, join_method = "identity_unverified", "symbol_only_rejected"

    return {
        "record_id": str(master_row.get("recordId") or "").strip(),
        "symbol": symbol,
        "company_name": company_name,
        "isin": isin,
        "universe_isin": stock["isin"],
        "reporting_date": reporting_date,
        "submission_date": _parse_nse_date(master_row.get("submissionDate"), field="submission date"),
        "broadcast_date": str(master_row.get("broadcastDate") or "").strip() or None,
        "system_date": str(master_row.get("systemDate") or "").strip() or None,
        "revised_data": str(master_row.get("revisedData") or "").strip() or None,
        "revision_date": _parse_nse_date(master_row.get("revisionDate"), field="revision date"),
        "revision_remark": str(master_row.get("revisionRemark") or "").strip() or None,
        "xbrl_url": str(master_row.get("xbrl") or "").strip(),
        "identity_status": identity_status,
        "identity_join_method": join_method,
    }


def _trusted_xbrl_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == TRUSTED_XBRL_HOST
        and parsed.path.startswith(TRUSTED_XBRL_PATH)
    )


def _write_json_snapshot(path: Path, value: dict) -> str:
    payload = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    digest = _sha256(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and _sha256(path.read_bytes()) != digest:
        raise RuntimeError(f"immutable shareholding snapshot collision: {path}")
    if not path.exists():
        path.write_bytes(payload)
    return digest


def _read_existing_filings(root: Path) -> dict[tuple[str, str], dict]:
    directory = root / "data/snapshots/shareholding_filings"
    existing = {}
    for path in directory.glob("filing_*.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        key = (
            str(value["filing"]["record_id"]),
            value["metadata_sha256"],
            int(value.get("parser_version", 1)),
        )
        if key in existing:
            raise ValueError(f"duplicate normalized shareholding filing snapshot: {key}")
        existing[key] = value
    return existing


def _latest_sort_key(metadata: dict) -> tuple:
    broadcast = _parse_nse_datetime(metadata.get("broadcast_date"), field="broadcast date")
    system = _parse_nse_datetime(metadata.get("system_date"), field="system date")
    submission = metadata.get("submission_date") or ""
    return (
        broadcast or datetime.min,
        system or datetime.min,
        submission,
        metadata["record_id"],
    )


def _facts_by_category(parsed: dict) -> dict[str, dict]:
    result = {}
    for row in parsed["category_records"]:
        key = row["category_key"]
        if key in result:
            raise ValueError(
                f"duplicate XBRL category record for {key} on {row['reporting_date']}"
            )
        result[key] = row
    return result


def _sum_if_present(left: object, right: object) -> str | None:
    if left is None or right is None:
        return None
    from decimal import Decimal
    return str(Decimal(str(left)) + Decimal(str(right)))


def _build_ownership_rows(
    universe: list[dict],
    master_records: list[dict],
    filings_by_key: dict[tuple[str, str], dict],
    master_hash: str,
) -> list[dict]:
    grouped = defaultdict(list)
    dates = sorted({row["reporting_date"] for row in master_records})
    for row in master_records:
        grouped[(row["symbol"], row["reporting_date"])].append(row)

    output = []
    for reporting_date in dates:
        for stock in universe:
            candidates = grouped.get((stock["symbol"], reporting_date), [])
            if not candidates:
                output.append({
                    "reporting_date": reporting_date,
                    "symbol": stock["symbol"],
                    "company_name": stock["company_name"],
                    "isin": stock["isin"],
                    "identity_status": "not_found_in_current_master",
                    "identity_join_method": None,
                    "filing_status": "not_found_in_current_master",
                    "source_master_sha256": master_hash,
                })
                continue
            latest = max(candidates, key=_latest_sort_key)
            key = (latest["record_id"], latest["metadata_sha256"], PARSER_VERSION)
            filing = filings_by_key.get(key)
            base = {
                "reporting_date": reporting_date,
                "symbol": stock["symbol"],
                "company_name": stock["company_name"],
                "isin": stock["isin"],
                "record_id": latest["record_id"],
                "submission_date": latest["submission_date"],
                "broadcast_date": latest["broadcast_date"],
                "system_date": latest["system_date"],
                "revised_data": latest["revised_data"],
                "revision_date": latest["revision_date"],
                "identity_status": latest["identity_status"],
                "identity_join_method": latest["identity_join_method"],
                "source_master_sha256": master_hash,
            }
            if latest["identity_status"] != "verified":
                output.append({**base, "filing_status": latest["identity_status"]})
                continue
            if filing is None:
                output.append({**base, "filing_status": "xbrl_fetch_failed"})
                continue

            parsed = filing["parsed"]
            categories = _facts_by_category(parsed)
            fpi_i = categories.get("fpi_category_i", {})
            fpi_ii = categories.get("fpi_category_ii", {})
            other_foreign = categories.get("other_foreign_institutions", {})
            overseas = categories.get("overseas_depositories", {})
            foreign_total = categories.get("foreign_institutions_total", {})
            reconciliation = next(
                (item for item in parsed["fpi_reconciliation"] if item["reporting_date"] == reporting_date),
                {},
            )
            output.append({
                **base,
                "filing_status": "parsed",
                "fpi_category_i_shares": fpi_i.get("shares"),
                "fpi_category_ii_shares": fpi_ii.get("shares"),
                "fpi_total_shares": _sum_if_present(fpi_i.get("shares"), fpi_ii.get("shares")),
                "fpi_category_i_ownership_percentage_points": fpi_i.get("ownership_percentage_points"),
                "fpi_category_ii_ownership_percentage_points": fpi_ii.get("ownership_percentage_points"),
                "fpi_total_ownership_percentage_points": _sum_if_present(
                    fpi_i.get("ownership_percentage_points"),
                    fpi_ii.get("ownership_percentage_points"),
                ),
                "other_foreign_institution_shares": other_foreign.get("shares"),
                "overseas_depositories_shares": overseas.get("shares"),
                "foreign_institutions_total_shares": foreign_total.get("shares"),
                "fpi_reconciliation_status": reconciliation.get("status"),
                "xbrl_sha256": filing["xbrl_sha256"],
            })
    return output


def ingest(root: Path = Path(".")) -> dict:
    root = root.resolve()
    universe, universe_by_symbol, universe_hash = _load_universe(root)

    bootstrap, bootstrap_meta = _get(PAGE, BASE + "/", "text/html,*/*")
    if not bootstrap or bootstrap_meta["http_status"] != 200:
        raise RuntimeError("NSE shareholding master session bootstrap failed")
    master_payload, master_meta = _get(MASTER_URL, PAGE, "application/json,*/*")
    if not master_payload or master_meta["http_status"] != 200:
        raise RuntimeError("NSE shareholding master endpoint returned an empty/non-200 response")
    try:
        decoded = json.loads(master_payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("NSE shareholding master endpoint did not return valid JSON") from exc
    if not isinstance(decoded, list) or not decoded:
        raise ValueError("NSE shareholding master must be a non-empty JSON list")
    master_hash = _sha256(master_payload)

    raw_dir = root / "data/raw/shareholding"
    raw_xbrl_dir = raw_dir / "xbrl"
    master_raw_path = raw_dir / f"shareholding_master_{master_hash[:16]}.json"
    raw_dir.mkdir(parents=True, exist_ok=True)
    if master_raw_path.exists() and _sha256(master_raw_path.read_bytes()) != master_hash:
        raise RuntimeError("immutable NSE shareholding master raw hash mismatch")
    if not master_raw_path.exists():
        master_raw_path.write_bytes(master_payload)

    current_records = []
    seen_record_ids = set()
    for row in decoded:
        symbol = str(row.get("symbol") or "").strip()
        stock = universe_by_symbol.get(symbol)
        if stock is None:
            continue
        metadata = _metadata(row, stock)
        record_id = metadata["record_id"]
        if not record_id:
            raise ValueError(f"shareholding master row has no recordId for {symbol}")
        if record_id in seen_record_ids:
            raise ValueError(f"duplicate shareholding recordId in master response: {record_id}")
        seen_record_ids.add(record_id)
        metadata_sha = _canonical_hash(metadata)
        current_records.append({
            **metadata,
            "metadata_sha256": metadata_sha,
            "source_master_sha256": master_hash,
        })
    if not current_records:
        raise ValueError("NSE shareholding master has no records for current Nifty 500 universe")

    retrieved_at = datetime.now(timezone.utc).isoformat()
    master_snapshot_value = {
        "dataset": "nse_shareholding_master_nifty500",
        "publisher": "National Stock Exchange of India",
        "source_url": MASTER_URL,
        "source_master_sha256": master_hash,
        "universe_sha256": universe_hash,
        "record_count": len(current_records),
        "records": current_records,
    }
    master_snapshot_payload = (
        json.dumps(master_snapshot_value, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    master_snapshot_hash = _sha256(master_snapshot_payload)
    reporting_date = max(row["reporting_date"] for row in current_records)
    master_snapshot_path = root / "data/snapshots/shareholding_master" / (
        f"shareholding_master_{reporting_date}_{master_snapshot_hash[:16]}.json"
    )
    _write_json_snapshot(master_snapshot_path, master_snapshot_value)

    existing = _read_existing_filings(root)
    filings_by_key = dict(existing)
    fetched_count = 0
    reused_count = 0
    fetch_errors = []
    raw_xbrl_refs = []
    eligible_records = [
        row for row in current_records if row["identity_status"] == "verified"
    ]

    for metadata in eligible_records:
        key = (metadata["record_id"], metadata["metadata_sha256"], PARSER_VERSION)
        if key in existing:
            reused_count += 1
            continue
        url = metadata["xbrl_url"]
        if not _trusted_xbrl_url(url):
            fetch_errors.append({
                "record_id": metadata["record_id"],
                "symbol": metadata["symbol"],
                "status": "untrusted_xbrl_url",
                "url": url,
            })
            continue
        try:
            payload, response_meta = _get(url, PAGE, "application/xml,text/xml,*/*")
            if not payload or response_meta["http_status"] != 200:
                raise RuntimeError("XBRL endpoint returned an empty/non-200 response")
            parsed = parse_xbrl(payload)
            if parsed["reporting_dates"] != [metadata["reporting_date"]]:
                raise ValueError(
                    f"XBRL reporting date mismatch for {metadata['symbol']}/"
                    f"{metadata['record_id']}: master={metadata['reporting_date']}, "
                    f"xbrl={parsed['reporting_dates']}"
                )
            xbrl_hash = _sha256(payload)
            raw_path = raw_xbrl_dir / f"{metadata['record_id']}_{xbrl_hash[:16]}.xml"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            if raw_path.exists() and _sha256(raw_path.read_bytes()) != xbrl_hash:
                raise RuntimeError(f"immutable XBRL raw hash mismatch: {raw_path}")
            if not raw_path.exists():
                raw_path.write_bytes(payload)
            raw_xbrl_refs.append({
                "path": str(raw_path.relative_to(root)),
                "sha256": xbrl_hash,
                "bytes": len(payload),
                "record_id": metadata["record_id"],
                "symbol": metadata["symbol"],
            })
            filing_value = {
                "dataset": "nse_shareholding_xbrl_filing",
                "parser_version": PARSER_VERSION,
                "filing": metadata,
                "metadata_sha256": metadata["metadata_sha256"],
                "source_master_sha256": master_hash,
                "xbrl_url": url,
                "xbrl_sha256": xbrl_hash,
                "xbrl_raw_path": str(raw_path.relative_to(root)),
                "xbrl_content_type": response_meta.get("content_type"),
                "parsed": parsed,
            }
            filing_path = root / "data/snapshots/shareholding_filings" / (
                f"filing_{metadata['record_id']}_{metadata['metadata_sha256'][:16]}_p{PARSER_VERSION}.json"
            )
            _write_json_snapshot(filing_path, filing_value)
            filings_by_key[key] = filing_value
            fetched_count += 1
            time.sleep(0.12)
        except Exception as exc:
            fetch_errors.append({
                "record_id": metadata["record_id"],
                "symbol": metadata["symbol"],
                "status": "fetch_or_parse_error",
                "error_type": type(exc).__name__,
                "error": str(exc),
            })

    ownership_rows = _build_ownership_rows(
        universe, current_records, filings_by_key, master_hash
    )
    ownership_payload_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(ownership_payload_buffer, fieldnames=OWNERSHIP_FIELDS)
    writer.writeheader()
    writer.writerows(ownership_rows)
    ownership_payload = ownership_payload_buffer.getvalue().encode("utf-8")
    ownership_hash = _sha256(ownership_payload)
    ownership_path = root / "data/snapshots/shareholding_ownership" / (
        f"shareholding_ownership_{reporting_date}_{ownership_hash[:16]}.csv"
    )
    ownership_path.parent.mkdir(parents=True, exist_ok=True)
    if ownership_path.exists() and _sha256(ownership_path.read_bytes()) != ownership_hash:
        raise RuntimeError("immutable shareholding ownership snapshot hash mismatch")
    if not ownership_path.exists():
        ownership_path.write_bytes(ownership_payload)

    ownership_current = root / "data/raw/shareholding/shareholding_ownership_current.csv"
    ownership_current.write_bytes(ownership_payload)
    identity_counts = Counter(row["identity_status"] for row in current_records)
    parsed_count = sum(row.get("filing_status") == "parsed" for row in ownership_rows)
    latest_reporting_date = max(row["reporting_date"] for row in current_records)
    latest_ownership_rows = [row for row in ownership_rows if row["reporting_date"] == latest_reporting_date]
    parsed_latest_count = sum(row.get("filing_status") == "parsed" for row in latest_ownership_rows)
    eligible_count = len(eligible_records)
    status = (
        "partial_xbrl_coverage" if fetch_errors
        else "partial_identity_coverage" if identity_counts.get("isin_mismatch", 0) or identity_counts.get("identity_unverified", 0)
        else "complete"
    )
    manifest = {
        "dataset": "nse_shareholding_fpi_ownership",
        "publisher": "National Stock Exchange of India",
        "retrieved_at_utc": retrieved_at,
        "status": status,
        "source_page": PAGE,
        "master_url": MASTER_URL,
        "master_raw_path": str(master_raw_path.relative_to(root)),
        "master_raw_sha256": master_hash,
        "universe_sha256": universe_hash,
        "current_universe_count": len(universe),
        "master_records_for_current_universe": len(current_records),
        "reporting_dates": sorted({row["reporting_date"] for row in current_records}),
        "identity_status_counts": dict(sorted(identity_counts.items())),
        "eligible_filing_count": eligible_count,
        "fetched_filing_count": fetched_count,
        "reused_filing_count": reused_count,
        "fetch_error_count": len(fetch_errors),
        "fetch_errors": fetch_errors,
        "raw_xbrl_refs": raw_xbrl_refs,
        "filing_snapshot_paths": [
            str(
                root / "data/snapshots/shareholding_filings"
                / f"filing_{row['record_id']}_{row['metadata_sha256'][:16]}_p{PARSER_VERSION}.json"
            ).replace(str(root) + "/", "")
            for row in eligible_records
            if (row["record_id"], row["metadata_sha256"]) in filings_by_key
        ],
        "parsed_ownership_row_count": parsed_count,
        "ownership_row_count": len(ownership_rows),
        "latest_reporting_date": latest_reporting_date,
        "latest_quarter_parsed_count": parsed_latest_count,
        "latest_quarter_coverage_pct": round(parsed_latest_count / len(universe) * 100, 2),
        "ownership_current_path": str(ownership_current.relative_to(root)),
        "ownership_snapshot_path": str(ownership_path.relative_to(root)),
        "ownership_snapshot_sha256": ownership_hash,
        "ownership_current_sha256": ownership_hash,
        "master_snapshot_path": str(master_snapshot_path.relative_to(root)),
        "master_snapshot_sha256": master_snapshot_hash,
        "parser_version": PARSER_VERSION,
        "source_boundaries": [
            "Only current Nifty 500 symbols are included in the current ownership view.",
            "ISIN mismatches are not joined; symbol-only joins require an exact normalized company-name match.",
            "All filing record IDs and revisions are retained append-only.",
            "Latest revision is selected by broadcast/system timestamp for the current quarter view.",
            "FPI Category I and II are extracted from XBRL dimensions; other foreign institutions remain separate.",
            "Quarter-end ownership is not a dated transaction and does not prove when shares were bought.",
            "Missing or unparsed categories remain null; they are never assumed to be zero.",
            "The master API may expose only the currently available filing period; older captured snapshots are retained locally.",
        ],
    }
    manifest_path = raw_dir / "current_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if fetch_errors:
        # Persist the partial result and let the workflow expose the coverage gap.
        pass
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(ingest(args.repo_root), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
