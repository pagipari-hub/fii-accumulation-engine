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
PARSER_VERSION = 4
PLACEHOLDER_CSV = Path("data/audits/nifty500_index_placeholders_2026-11-09.csv")

ALLOWED_CROSSWALK_STATUSES = {
    "verified_exact_dated_bse_scripcode_isin_match",
    "verified_exact_dated_bse_symbol_isin_match",
}

OWNERSHIP_FIELDS = [
    "reporting_date", "symbol", "company_name", "isin",
    "universe_member_type", "tradability_status", "fpi_scoring_eligible",
    "universe_exclusion_reason", "record_id", "submission_date", "broadcast_date", "system_date",
    "revised_data", "revision_date", "identity_status", "identity_join_method",
    "filing_status", "fpi_category_i_shares", "fpi_category_ii_shares",
    "fpi_total_shares", "fpi_category_i_ownership_percentage_points",
    "fpi_category_ii_ownership_percentage_points",
    "fpi_total_ownership_percentage_points",
    "other_foreign_institution_shares", "overseas_depositories_shares",
    "foreign_direct_investment_shares", "foreign_venture_capital_investor_shares",
    "sovereign_wealth_funds_foreign_shares", "foreign_institutions_total_shares",
    "fpi_reconciliation_status", "xbrl_sha256", "source_master_sha256",
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


def _load_index_placeholders(root: Path, universe: list[dict]) -> tuple[dict[str, dict], dict | None]:
    path = root / PLACEHOLDER_CSV
    if not path.exists():
        return {}, None
    payload = path.read_bytes()
    rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
    symbols = {row["symbol"].strip().upper() for row in universe}
    indexed = {}
    for row in rows:
        symbol = row.get("symbol", "").strip().upper()
        if not symbol or symbol not in symbols or symbol in indexed:
            raise ValueError(f"invalid or duplicate index placeholder symbol: {symbol}")
        if row.get("classification") != "demerged_index_placeholder":
            raise ValueError(f"unsupported index placeholder classification: {symbol}")
        indexed[symbol] = row
    return indexed, {
        "path": str(PLACEHOLDER_CSV),
        "sha256": _sha256(payload),
        "placeholder_count": len(indexed),
        "symbols": sorted(indexed),
    }


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


def _load_identity_crosswalk(path: Path | None) -> tuple[dict[str, dict], dict | None]:
    if path is None:
        return {}, None
    payload = path.read_bytes()
    rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
    if not rows:
        raise ValueError("dated BSE identity crosswalk is empty")
    report_path = path.with_suffix(".json")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    digest = str(report.get("bse_source_sha256") or "").strip()
    if report.get("bse_source_http_status") != 200:
        raise ValueError("dated BSE identity source was not HTTP 200")
    if report.get("candidate_count") != len(rows):
        raise ValueError("dated BSE identity crosswalk candidate count mismatch")
    if report.get("verified_count", -1) + report.get("unresolved_count", -1) != len(rows):
        raise ValueError("dated BSE identity crosswalk verified/unresolved counts do not reconcile")
    if not digest:
        raise ValueError("dated BSE identity crosswalk has no source hash")
    source_reference = str(report.get("bse_source_path") or "").strip()
    source_name = Path(source_reference).name
    if not source_name:
        raise ValueError("dated BSE identity report has no safe raw-source filename")
    source_path = report_path.parent / source_name
    if not source_path.is_file() or _sha256(source_path.read_bytes()) != digest:
        raise ValueError("dated BSE raw source is missing or fails SHA-256 verification")
    indexed = {}
    for row in rows:
        symbol = row.get("symbol", "").strip().upper()
        status = row.get("bse_crosswalk_status", "")
        if not symbol or symbol in indexed:
            raise ValueError("dated BSE identity crosswalk has blank or duplicate symbols")
        if status not in ALLOWED_CROSSWALK_STATUSES:
            continue
        if row.get("reporting_date") != report.get("reporting_date"):
            raise ValueError(f"dated BSE identity crosswalk date mismatch: {symbol}")
        if row.get("bse_bhavcopy_sha256") != digest:
            raise ValueError(f"dated BSE source hash mismatch in crosswalk row: {symbol}")
        if row.get("bse_isin", "").strip().upper() != row.get("universe_isin", "").strip().upper():
            raise ValueError(f"dated BSE ISIN does not match universe ISIN: {symbol}")
        if len(row.get("xbrl_sha256", "")) != 64:
            raise ValueError(f"crosswalk row has no valid XBRL hash: {symbol}")
        indexed[symbol] = row
    if len(indexed) != report.get("verified_count"):
        raise ValueError("dated BSE identity crosswalk verified row count mismatch")
    return indexed, {
        "path": str(path),
        "sha256": _sha256(payload),
        "report_path": str(report_path),
        "reporting_date": report["reporting_date"],
        "candidate_count": len(rows),
        "verified_count": len(indexed),
        "unresolved_count": len(rows) - len(indexed),
        "bse_source_sha256": digest,
    }


def _metadata(master_row: dict, stock: dict, identity_evidence: dict | None = None) -> dict:
    reporting_date = _parse_nse_date(master_row.get("date"), field="reporting date")
    if reporting_date is None:
        raise ValueError(f"missing reporting date for {master_row.get('symbol')}")
    symbol = str(master_row.get("symbol") or "").strip()
    isin = str(master_row.get("isin") or "").strip() or None
    company_name = str(master_row.get("name") or "").strip()
    identity_evidence_xbrl_sha256 = None
    identity_evidence_bse_sha256 = None
    if isin and isin == stock["isin"]:
        identity_status, join_method = "verified", "isin_exact"
    elif identity_evidence:
        exact_evidence = (
            identity_evidence.get("reporting_date") == reporting_date
            and identity_evidence.get("master_record_id") == str(master_row.get("recordId") or "").strip()
            and identity_evidence.get("master_xbrl_url") == str(master_row.get("xbrl") or "").strip()
            and identity_evidence.get("universe_isin", "").strip().upper() == stock["isin"].upper()
            and identity_evidence.get("bse_isin", "").strip().upper() == stock["isin"].upper()
            and identity_evidence.get("bse_crosswalk_status") in ALLOWED_CROSSWALK_STATUSES
        )
        if exact_evidence:
            identity_status = "verified"
            join_method = identity_evidence["bse_crosswalk_status"]
            identity_evidence_xbrl_sha256 = identity_evidence["xbrl_sha256"]
            identity_evidence_bse_sha256 = identity_evidence["bse_bhavcopy_sha256"]
        elif isin:
            identity_status, join_method = "isin_mismatch", "dated_bse_crosswalk_rejected"
        elif _normal_name(company_name) == _normal_name(stock["company_name"]):
            identity_status, join_method = "verified", "symbol_and_company_name"
        else:
            identity_status, join_method = "identity_unverified", "dated_bse_crosswalk_rejected"
    elif isin:
        identity_status, join_method = "isin_mismatch", "symbol_only_rejected"
    elif _normal_name(company_name) == _normal_name(stock["company_name"]):
        identity_status, join_method = "verified", "symbol_and_company_name"
    else:
        identity_status, join_method = "identity_unverified", "symbol_only_rejected"

    result = {
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
    if identity_evidence_xbrl_sha256:
        result["identity_evidence_xbrl_sha256"] = identity_evidence_xbrl_sha256
        result["identity_evidence_bse_sha256"] = identity_evidence_bse_sha256
    return result


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
    placeholders: dict[str, dict] | None = None,
) -> list[dict]:
    grouped = defaultdict(list)
    dates = sorted({row["reporting_date"] for row in master_records})
    for row in master_records:
        grouped[(row["symbol"], row["reporting_date"])].append(row)

    output = []
    for reporting_date in dates:
        for stock in universe:
            placeholder = (placeholders or {}).get(stock["symbol"].upper())
            if placeholder:
                output.append({
                    "reporting_date": reporting_date,
                    "symbol": stock["symbol"],
                    "company_name": stock["company_name"],
                    "isin": stock["isin"],
                    "universe_member_type": "index_adjustment_placeholder",
                    "tradability_status": "non_tradable_zero_price",
                    "fpi_scoring_eligible": "false",
                    "universe_exclusion_reason": placeholder.get("reason", ""),
                    "identity_status": "not_applicable_index_placeholder",
                    "identity_join_method": "index_corporate_action_zero_price_placeholder",
                    "filing_status": "not_applicable_index_placeholder",
                    "source_master_sha256": master_hash,
                })
                continue
            candidates = grouped.get((stock["symbol"], reporting_date), [])
            if not candidates:
                output.append({
                    "reporting_date": reporting_date,
                    "symbol": stock["symbol"],
                    "company_name": stock["company_name"],
                    "isin": stock["isin"],
                    "universe_member_type": "tradable_security",
                    "tradability_status": "tradable",
                    "fpi_scoring_eligible": "true",
                    "universe_exclusion_reason": "",
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
                "universe_member_type": "tradable_security",
                "tradability_status": "tradable",
                "fpi_scoring_eligible": "true",
                "universe_exclusion_reason": "",
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
            foreign_direct = categories.get("foreign_direct_investment", {})
            foreign_vc = categories.get("foreign_venture_capital_investors", {})
            foreign_swf = categories.get("sovereign_wealth_funds_foreign", {})
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
                "foreign_direct_investment_shares": foreign_direct.get("shares"),
                "foreign_venture_capital_investor_shares": foreign_vc.get("shares"),
                "sovereign_wealth_funds_foreign_shares": foreign_swf.get("shares"),
                "foreign_institutions_total_shares": foreign_total.get("shares"),
                "fpi_reconciliation_status": reconciliation.get("status"),
                "xbrl_sha256": filing["xbrl_sha256"],
            })
    return output


def ingest(root: Path = Path("."), identity_crosswalk_path: Path | None = None) -> dict:
    root = root.resolve()
    universe, universe_by_symbol, universe_hash = _load_universe(root)
    index_placeholders, placeholder_meta = _load_index_placeholders(root, universe)
    if identity_crosswalk_path is not None and not identity_crosswalk_path.is_absolute():
        identity_crosswalk_path = root / identity_crosswalk_path
    identity_crosswalk, identity_crosswalk_meta = _load_identity_crosswalk(identity_crosswalk_path)

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
        metadata = _metadata(row, stock, identity_crosswalk.get(symbol.upper()))
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
            expected_identity_xbrl_hash = metadata.get("identity_evidence_xbrl_sha256")
            if expected_identity_xbrl_hash and xbrl_hash != expected_identity_xbrl_hash:
                raise ValueError("XBRL payload hash differs from dated BSE identity evidence")
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
        universe, current_records, filings_by_key, master_hash, index_placeholders
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
    identity_counts = Counter(row["identity_status"] for row in ownership_rows)
    parsed_count = sum(row.get("filing_status") == "parsed" for row in ownership_rows)
    latest_reporting_date = max(row["reporting_date"] for row in current_records)
    latest_ownership_rows = [row for row in ownership_rows if row["reporting_date"] == latest_reporting_date]
    parsed_latest_count = sum(row.get("filing_status") == "parsed" for row in latest_ownership_rows)
    eligible_count = len(eligible_records)
    tradable_universe_count = len(universe) - len(index_placeholders)
    status = (
        "partial_xbrl_coverage" if fetch_errors
        else "partial_identity_coverage" if identity_counts.get("isin_mismatch", 0) or identity_counts.get("identity_unverified", 0) or identity_counts.get("not_found_in_current_master", 0)
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
        "identity_crosswalk": identity_crosswalk_meta,
        "index_placeholder_classification": placeholder_meta,
        "current_universe_count": len(universe),
        "current_tradable_universe_count": tradable_universe_count,
        "index_placeholder_count": len(index_placeholders),
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
            if (row["record_id"], row["metadata_sha256"], PARSER_VERSION) in filings_by_key
        ],
        "parsed_ownership_row_count": parsed_count,
        "ownership_row_count": len(ownership_rows),
        "latest_reporting_date": latest_reporting_date,
        "latest_quarter_parsed_count": parsed_latest_count,
        "latest_quarter_coverage_pct": round(parsed_latest_count / tradable_universe_count * 100, 2),
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
            "Demerger index placeholders included at zero price remain in the 501-row index snapshot but are excluded from tradable-stock ownership coverage and FPI scoring.",
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
    parser.add_argument("--identity-crosswalk", type=Path)
    args = parser.parse_args()
    print(json.dumps(
        ingest(args.repo_root, args.identity_crosswalk), indent=2, ensure_ascii=False
    ))


if __name__ == "__main__":
    main()
