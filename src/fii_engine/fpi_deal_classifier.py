"""Classify named NSE bulk/block deal counterparties against the CDSL FPI register."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fii_engine.cdsl_fpi_registry import normalize_registry_name

PARSER_VERSION = 1
REGISTRY_MANIFEST = Path("data/raw/cdsl_fpi_registry/cdsl_fpi_registry_current_manifest.json")
UNIVERSE_CSV = Path("data/raw/universe/nifty500_current.csv")
DEAL_MANIFEST_DIR = Path("data/raw/bulk_block_deals/manifests")
EVENT_FIELDS = [
    "event_id", "trade_date", "symbol", "security_name", "deal_type",
    "source_isin", "matched_universe_isin", "universe_join_method",
    "nifty500_snapshot_membership_status", "nifty500_snapshot_member_verified",
    "universe_company_name", "universe_industry", "universe_series",
    "client_name", "client_normalized_name", "source_client_names", "source_security_names",
    "side", "quantity", "weighted_average_price", "deal_value",
    "registry_match_status", "name_match_type", "counterparty_class",
    "registration_numbers", "valid_upto_dates", "registry_record_count",
    "registry_snapshot_date", "temporal_status", "cross_report_duplicate",
    "source_record_count", "source_report_types", "source_files",
    "source_fingerprints", "source_remarks",
]
STOCK_FIELDS = [
    "trade_date_window_start", "trade_date_window_end", "symbol",
    "universe_company_name", "universe_industry", "universe_series", "universe_isin",
    "nifty500_snapshot_membership_status", "nifty500_snapshot_member_verified",
    "deal_data_status", "deal_event_count", "unverified_deal_event_count",
    "total_deal_event_count", "source_record_count", "unverified_source_record_count",
    "cross_report_duplicate_event_count",
    "current_registry_matched_fpi_client_event_count", "current_registry_matched_odi_issuer_event_count",
    "ambiguous_registration_event_count", "unmatched_event_count",
    "current_registry_matched_fpi_client_buy_quantity", "current_registry_matched_fpi_client_sell_quantity",
    "current_registry_matched_fpi_client_known_net_quantity",
    "current_registry_matched_fpi_client_buy_value", "current_registry_matched_fpi_client_sell_value",
    "current_registry_matched_odi_issuer_buy_quantity", "current_registry_matched_odi_issuer_sell_quantity",
    "unmatched_buy_quantity", "unmatched_sell_quantity",
    "temporally_verified_fpi_buy_quantity", "temporally_verified_fpi_sell_quantity",
    "temporally_unverified_fpi_buy_quantity", "temporally_unverified_fpi_sell_quantity",
    "counterparty_identity_coverage_pct", "known_fpi_net_status", "registry_temporal_status",
]
OUTPUT_PATHS = {
    "events": "data/raw/fpi_deal_attribution/fpi_deal_counterparty_current.csv",
    "stocks": "data/raw/fpi_deal_attribution/fpi_deal_stock_summary_current.csv",
    "manifest": "data/raw/fpi_deal_attribution/fpi_deal_attribution_current_manifest.json",
}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"CSV missing header: {path}")
        return list(reader)


def _csv_bytes(rows: list[dict], fields: list[str]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _write_immutable(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise RuntimeError(f"immutable FPI deal snapshot collision: {path}")
        return
    path.write_bytes(payload)


def _surface_name(value: str) -> str:
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", value.upper()).split())


def _decimal(value: str, field: str) -> Decimal:
    try:
        result = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal in deal field {field}: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"non-finite decimal in deal field {field}")
    return result


def _load_registry(root: Path) -> tuple[dict, dict[str, list[dict]]]:
    manifest_path = root / REGISTRY_MANIFEST
    if not manifest_path.exists():
        raise FileNotFoundError(f"CDSL FPI registry manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "complete" or manifest.get("successful_letter_pages") != 26:
        raise ValueError("CDSL FPI registry snapshot is not complete")
    csv_path = root / manifest["normalized_csv_path"]
    payload = csv_path.read_bytes()
    if _sha256(payload) != manifest["normalized_csv_sha256"]:
        raise ValueError("CDSL FPI registry normalized CSV hash mismatch")
    records = _read_csv(csv_path)
    required = {
        "registered_name", "normalized_name", "registration_no",
        "valid_upto_date", "source_letter",
    }
    if not records or not required.issubset(records[0]):
        raise ValueError("CDSL FPI registry CSV schema is incomplete")
    index: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        expected = normalize_registry_name(record["registered_name"])
        if expected != record["normalized_name"]:
            raise ValueError(
                f"CDSL registry normalized-name mismatch for {record['registered_name']!r}"
            )
        index[record["normalized_name"]].append(record)
    return manifest, index



def _load_universe(
    root: Path,
) -> tuple[dict[str, dict], dict[str, dict], dict[str, list[dict]], dict]:
    path = root / UNIVERSE_CSV
    payload = path.read_bytes()
    rows = _read_csv(path)
    required = {"symbol", "company_name", "industry", "series", "isin"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("current Nifty 500 universe CSV schema is incomplete")
    if not 480 <= len(rows) <= 520:
        raise ValueError(f"unexpected Nifty 500 universe row count: {len(rows)}")
    symbols = [row["symbol"].strip().upper() for row in rows]
    isins = [row["isin"].strip().upper() for row in rows]
    if any(not value for value in symbols) or any(not value for value in isins):
        raise ValueError("blank symbol or ISIN in current Nifty 500 universe")
    if len(set(symbols)) != len(symbols):
        raise ValueError("duplicate symbols in current Nifty 500 universe")
    if len(set(isins)) != len(isins):
        raise ValueError("duplicate ISINs in current Nifty 500 universe")
    by_symbol = {row["symbol"].strip().upper(): row for row in rows}
    by_isin = {row["isin"].strip().upper(): row for row in rows}
    by_company_name: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_company_name[normalize_registry_name(row["company_name"])].append(row)
    csv_sha = _sha256(payload)
    manifest_dir = root / "data/snapshots/universe"
    matching_manifests = []
    for manifest_path in sorted(manifest_dir.glob("nifty500_*.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("normalized_sha256") == csv_sha:
            matching_manifests.append((manifest_path, manifest))
    if len(matching_manifests) != 1:
        raise ValueError(
            "current Nifty 500 CSV must match exactly one immutable snapshot manifest"
        )
    manifest_path, snapshot_manifest = matching_manifests[0]
    metadata = {
        "path": str(UNIVERSE_CSV),
        "sha256": csv_sha,
        "row_count": len(rows),
        "snapshot_manifest_path": str(manifest_path.relative_to(root)),
        "retrieved_at_utc": snapshot_manifest["retrieved_at_utc"],
        "source_freshness_statement": snapshot_manifest["source_freshness_statement"],
        "source_freshness_semantics": snapshot_manifest["source_freshness_semantics"],
        "publisher": snapshot_manifest["publisher"],
        "source_page": snapshot_manifest["source_page"],
        "source_download_url": snapshot_manifest["source_download_url"],
    }
    return by_isin, by_symbol, by_company_name, rows, metadata


def _universe_membership(
    event: dict,
    universe_by_isin: dict[str, dict] | None,
    universe_by_symbol: dict[str, dict] | None,
    universe_by_company_name: dict[str, list[dict]] | None = None,
) -> dict:
    if (
        universe_by_isin is None
        or universe_by_symbol is None
        or universe_by_company_name is None
    ):
        return {
            "source_isin": str(event.get("source_isin", "")),
            "matched_universe_isin": "",
            "universe_join_method": "not_checked",
            "nifty500_snapshot_membership_status": "universe_not_checked",
            "nifty500_snapshot_member_verified": "false",
            "universe_company_name": "",
            "universe_industry": "",
            "universe_series": "",
        }

    symbol = event["symbol"].strip().upper()
    source_isin = str(event.get("source_isin", "")).strip().upper()
    security_name = str(event.get("security_name", "")).strip()
    normalized_security_name = normalize_registry_name(security_name)
    universe_row = None
    if source_isin and source_isin in universe_by_isin:
        universe_row = universe_by_isin[source_isin]
        name_matches = (
            normalized_security_name
            == normalize_registry_name(universe_row["company_name"])
        )
        symbol_matches = universe_row["symbol"].strip().upper() == symbol
        if symbol_matches and name_matches:
            status, method, verified = (
                "nifty500_isin_symbol_company_match",
                "source_isin_symbol_and_company_name",
                "true",
            )
        elif symbol_matches:
            status, method, verified = (
                "nifty500_isin_symbol_match_company_name_mismatch",
                "source_isin_and_symbol",
                "true",
            )
        elif name_matches:
            status, method, verified = (
                "nifty500_isin_company_name_match_symbol_mismatch",
                "source_isin_and_company_name",
                "true",
            )
        else:
            status, method, verified = (
                "nifty500_isin_match_symbol_and_company_mismatch",
                "source_isin_only_conflict",
                "false",
            )
    elif symbol in universe_by_symbol:
        universe_row = universe_by_symbol[symbol]
        universe_name = normalize_registry_name(universe_row["company_name"])
        if normalized_security_name and normalized_security_name == universe_name:
            status, method, verified = (
                "nifty500_symbol_company_name_exact_normalized_match",
                "symbol_and_company_name_exact_normalized",
                "true",
            )
        else:
            status, method, verified = (
                "symbol_match_company_name_mismatch",
                "symbol_only_not_accepted",
                "false",
            )
    else:
        same_name = universe_by_company_name.get(normalized_security_name, [])
        status = (
            "company_name_match_symbol_mismatch"
            if len(same_name) == 1
            else "ambiguous_company_name_match_symbol_mismatch"
            if len(same_name) > 1
            else "outside_current_nifty500"
        )
        method, verified = "company_name_only_not_accepted", "false"

    return {
        "source_isin": source_isin,
        "matched_universe_isin": universe_row.get("isin", "") if universe_row else "",
        "universe_join_method": method,
        "nifty500_snapshot_membership_status": status,
        "nifty500_snapshot_member_verified": verified,
        "universe_company_name": universe_row.get("company_name", "") if universe_row else "",
        "universe_industry": universe_row.get("industry", "") if universe_row else "",
        "universe_series": universe_row.get("series", "") if universe_row else "",
    }

def _load_latest_deal_manifests(
    root: Path,
    lookback_days: int,
    requested_start: date | None = None,
    requested_end: date | None = None,
) -> tuple[list[dict], date, date]:
    manifest_dir = root / DEAL_MANIFEST_DIR
    if not manifest_dir.exists():
        raise FileNotFoundError(f"NSE bulk/block manifest directory missing: {manifest_dir}")
    latest: dict[tuple[str, str], tuple[dict, Path]] = {}
    all_dates = []
    for path in sorted(manifest_dir.glob("*.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        deal_type = str(manifest.get("deal_type", "")).lower()
        trade_date = str(manifest.get("trade_date", ""))
        if deal_type not in {"bulk", "block"} or not trade_date:
            continue
        date.fromisoformat(trade_date)
        all_dates.append(trade_date)
        key = (deal_type, trade_date)
        current = latest.get(key)
        if current is None or manifest.get("retrieved_at_utc", "") > current[0].get("retrieved_at_utc", ""):
            latest[key] = (manifest, path)
        elif (
            manifest.get("retrieved_at_utc", "") == current[0].get("retrieved_at_utc", "")
            and manifest.get("snapshot_sha256") != current[0].get("snapshot_sha256")
        ):
            raise ValueError(f"conflicting latest NSE deal manifests for {key}")
    if not all_dates:
        raise ValueError("no valid NSE bulk/block deal manifests found")
    if requested_start is not None or requested_end is not None:
        if requested_start is None or requested_end is None:
            raise ValueError("explicit deal window requires both from-date and to-date")
        start_date, end_date = requested_start, requested_end
        if start_date > end_date:
            raise ValueError("deal from-date must not be after to-date")
        if (end_date - start_date).days > 365:
            raise ValueError("explicit deal window must not exceed 365 calendar days")
    else:
        end_date = max(date.fromisoformat(value) for value in all_dates)
        start_date = end_date - timedelta(days=lookback_days)
    selected = [
        (manifest, path)
        for (deal_type, trade_date), (manifest, path) in latest.items()
        if start_date <= date.fromisoformat(trade_date) <= end_date
    ]
    selected.sort(key=lambda item: (
        item[0]["trade_date"], item[0]["deal_type"], item[0].get("retrieved_at_utc", "")
    ))
    return selected, start_date, end_date


def _load_deal_rows(root: Path, manifests: list[tuple[dict, Path]]) -> tuple[list[dict], list[dict]]:
    rows = []
    source_refs = []
    for manifest, manifest_path in manifests:
        snapshot_path = root / manifest["snapshot_path"]
        payload = snapshot_path.read_bytes()
        digest = _sha256(payload)
        if digest != manifest["snapshot_sha256"]:
            raise ValueError(f"NSE deal snapshot hash mismatch: {snapshot_path}")
        source_refs.append({
            "manifest_path": str(manifest_path.relative_to(root)),
            "manifest_sha256": _sha256(manifest_path.read_bytes()),
            "snapshot_path": manifest["snapshot_path"],
            "snapshot_sha256": digest,
            "deal_type": manifest["deal_type"],
            "trade_date": manifest["trade_date"],
            "retrieved_at_utc": manifest.get("retrieved_at_utc", ""),
        })
        for row in _read_csv(snapshot_path):
            if row.get("trade_date") != manifest["trade_date"]:
                raise ValueError(f"deal snapshot date mismatch: {snapshot_path}")
            if row.get("deal_type") != manifest["deal_type"]:
                raise ValueError(f"deal snapshot type mismatch: {snapshot_path}")
            row["_source_file"] = str(manifest["snapshot_path"])
            rows.append(row)
    return rows, source_refs


def _deduplicate_cross_report_rows(rows: list[dict]) -> list[dict]:
    grouped: dict[tuple, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        key = (
            row["trade_date"], row["symbol"], row.get("isin", ""), row["security_name"],
            _surface_name(row["client_name"]), row["side"],
            row["quantity"], row["weighted_average_price"],
        )
        grouped[key][row["deal_type"]].append(row)

    events = []
    for key, by_type in sorted(grouped.items(), key=lambda item: item[0]):
        for deal_type_rows in by_type.values():
            deal_type_rows.sort(key=lambda row: (
                row.get("record_fingerprint", ""), row.get("_source_file", "")
            ))
        occurrence_count = max(len(values) for values in by_type.values())
        for occurrence in range(occurrence_count):
            source_rows = [
                values[occurrence]
                for _, values in sorted(by_type.items())
                if occurrence < len(values)
            ]
            canonical = source_rows[0]
            client_norm = normalize_registry_name(canonical["client_name"])
            event_key = json.dumps(
                [*key, occurrence], ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            event_id = _sha256(event_key)[:24]
            events.append({
                "event_id": event_id,
                "trade_date": canonical["trade_date"],
                "symbol": canonical["symbol"],
                "source_isin": canonical.get("isin", ""),
                "security_name": canonical["security_name"],
                "deal_type": ";".join(sorted({row["deal_type"] for row in source_rows})),
                "client_name": canonical["client_name"],
                "client_normalized_name": client_norm,
                "source_client_names": ";".join(sorted({row["client_name"] for row in source_rows})),
                "source_security_names": ";".join(sorted({row["security_name"] for row in source_rows})),
                "side": canonical["side"],
                "quantity": canonical["quantity"],
                "weighted_average_price": canonical["weighted_average_price"],
                "deal_value": format(
                    Decimal(canonical["quantity"])
                    * Decimal(canonical["weighted_average_price"]),
                    ".2f",
                ),
                "cross_report_duplicate": str(len(source_rows) > 1).lower(),
                "source_record_count": len(source_rows),
                "source_report_types": ";".join(sorted({row["deal_type"] for row in source_rows})),
                "source_files": ";".join(sorted({row["_source_file"] for row in source_rows})),
                "source_fingerprints": ";".join(sorted({
                    row["record_fingerprint"] for row in source_rows
                })),
                "source_remarks": ";".join(sorted({
                    row.get("remarks", "") for row in source_rows
                })),
            })
    return events


def _temporal_status(
    trade_date: date, snapshot_date: date, validity_dates: list[str]
) -> str:
    parsed = [date.fromisoformat(value) for value in validity_dates if value]
    if snapshot_date == trade_date:
        if parsed and all(value < trade_date for value in parsed):
            return "expired_on_trade_date"
        if parsed:
            return "observed_in_registry_on_trade_date"
        return "observed_on_trade_date_validity_end_unknown"
    if parsed and all(value < trade_date for value in parsed):
        return "validity_end_before_trade_date"
    if snapshot_date > trade_date:
        return "post_trade_snapshot_registration_start_unknown"
    return "pre_trade_snapshot_revocation_status_unknown"


def _classify_event(
    event: dict,
    registry_index: dict[str, list[dict]],
    registry_snapshot_date: date,
    universe_by_isin: dict[str, dict] | None = None,
    universe_by_symbol: dict[str, dict] | None = None,
    universe_by_company_name: dict[str, list[dict]] | None = None,
) -> dict:
    universe_fields = _universe_membership(
        event, universe_by_isin, universe_by_symbol, universe_by_company_name
    )
    candidates = registry_index.get(event["client_normalized_name"], [])
    if not candidates:
        return {
            **event,
            "registry_match_status": "unmatched_current_registry",
            "name_match_type": "no_exact_normalized_match",
            "counterparty_class": "unmatched",
            "registration_numbers": "",
            "valid_upto_dates": "",
            "registry_record_count": 0,
            "registry_snapshot_date": registry_snapshot_date.isoformat(),
            "temporal_status": "not_established",
            **universe_fields,
        }

    unique_registrations = sorted({row["registration_no"] for row in candidates})
    valid_dates = sorted({row["valid_upto_date"] for row in candidates if row["valid_upto_date"]})
    registry_surface_names = {_surface_name(row["registered_name"]) for row in candidates}
    name_match_type = (
        "case_punctuation_normalized"
        if _surface_name(event["client_name"]) in registry_surface_names
        else "legal_suffix_normalized"
    )
    if len(unique_registrations) > 1:
        match_status = "multiple_registration_numbers"
        counterparty_class = "current_registry_match_ambiguous_registration"
    else:
        match_status = "current_registry_match"
        counterparty_class = (
            "current_registry_matched_odi_issuer" if re.search(r"\bODI\b", event["client_name"], re.IGNORECASE)
            else "current_registry_matched_fpi_client"
        )
    temporal = _temporal_status(
        date.fromisoformat(event["trade_date"]),
        registry_snapshot_date,
        valid_dates,
    )
    return {
        **event,
        "registry_match_status": match_status,
        "name_match_type": name_match_type,
        "counterparty_class": counterparty_class,
        "registration_numbers": ";".join(unique_registrations),
        "valid_upto_dates": ";".join(valid_dates),
        "registry_record_count": len(candidates),
        "registry_snapshot_date": registry_snapshot_date.isoformat(),
        "temporal_status": temporal,
        **universe_fields,
    }


def _stock_summary(
    events: list[dict],
    start_date: date,
    end_date: date,
    universe_rows: list[dict],
) -> list[dict]:
    events_by_symbol: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        events_by_symbol[event["symbol"]].append(event)

    results = []
    for universe_row in sorted(universe_rows, key=lambda row: row["symbol"]):
        symbol = universe_row["symbol"]
        all_items = events_by_symbol.get(symbol, [])
        items = [
            event for event in all_items
            if event.get("nifty500_snapshot_member_verified") == "true"
        ]
        unverified_items = [
            event for event in all_items
            if event.get("nifty500_snapshot_member_verified") != "true"
        ]
        totals = defaultdict(Decimal)
        counts = defaultdict(int)
        source_record_count = 0
        unverified_source_record_count = sum(
            int(event["source_record_count"]) for event in unverified_items
        )
        duplicate_count = 0

        for event in items:
            qty = Decimal(event["quantity"])
            value = Decimal(event["deal_value"])
            source_record_count += int(event["source_record_count"])
            duplicate_count += event["cross_report_duplicate"] == "true"
            counterparty = event["counterparty_class"]
            side = event["side"].lower()
            counts[counterparty] += 1
            if counterparty == "current_registry_matched_fpi_client":
                totals[f"fpi_{side}_quantity"] += qty
                totals[f"fpi_{side}_value"] += value
                if event["temporal_status"] == "observed_in_registry_on_trade_date":
                    totals[f"verified_fpi_{side}_quantity"] += qty
                else:
                    totals[f"unverified_fpi_{side}_quantity"] += qty
            elif counterparty == "current_registry_matched_odi_issuer":
                totals[f"odi_{side}_quantity"] += qty
            elif counterparty == "unmatched":
                totals[f"unmatched_{side}_quantity"] += qty
            elif counterparty == "current_registry_match_ambiguous_registration":
                totals[f"ambiguous_{side}_quantity"] += qty

        total_events = len(items)
        identified = (
            counts["current_registry_matched_fpi_client"]
            + counts["current_registry_matched_odi_issuer"]
            + counts["current_registry_match_ambiguous_registration"]
        )
        unmatched = counts["unmatched"]
        if not all_items:
            deal_status = "no_disclosed_deal_in_available_snapshots"
            net_status = "no_disclosed_deal_in_available_snapshots"
        elif not items:
            deal_status = "deal_records_present_but_universe_join_unverified"
            net_status = "deal_records_present_but_universe_join_unverified"
        else:
            deal_status = "observed_disclosed_deals"
            if (
                unmatched == 0
                and counts["current_registry_match_ambiguous_registration"] == 0
                and counts["current_registry_matched_odi_issuer"] == 0
            ):
                net_status = "complete_for_current_registry_matched_counterparties"
            else:
                net_status = "partial_unmatched_or_odi_counterparties"

        results.append({
            "trade_date_window_start": start_date.isoformat(),
            "trade_date_window_end": end_date.isoformat(),
            "symbol": symbol,
            "universe_company_name": universe_row["company_name"],
            "universe_industry": universe_row["industry"],
            "universe_series": universe_row["series"],
            "universe_isin": universe_row["isin"],
            "nifty500_snapshot_membership_status": "member_in_latest_published_snapshot",
            "nifty500_snapshot_member_verified": "true",
            "deal_data_status": deal_status,
            "deal_event_count": total_events,
            "unverified_deal_event_count": len(unverified_items),
            "total_deal_event_count": len(all_items),
            "source_record_count": source_record_count,
            "unverified_source_record_count": unverified_source_record_count,
            "cross_report_duplicate_event_count": duplicate_count,
            "current_registry_matched_fpi_client_event_count": counts["current_registry_matched_fpi_client"],
            "current_registry_matched_odi_issuer_event_count": counts["current_registry_matched_odi_issuer"],
            "ambiguous_registration_event_count": counts["current_registry_match_ambiguous_registration"],
            "unmatched_event_count": unmatched,
            "current_registry_matched_fpi_client_buy_quantity": str(int(totals["fpi_buy_quantity"])),
            "current_registry_matched_fpi_client_sell_quantity": str(int(totals["fpi_sell_quantity"])),
            "current_registry_matched_fpi_client_known_net_quantity": str(int(
                totals["fpi_buy_quantity"] - totals["fpi_sell_quantity"]
            )),
            "current_registry_matched_fpi_client_buy_value": format(totals["fpi_buy_value"], ".2f"),
            "current_registry_matched_fpi_client_sell_value": format(totals["fpi_sell_value"], ".2f"),
            "current_registry_matched_odi_issuer_buy_quantity": str(int(totals["odi_buy_quantity"])),
            "current_registry_matched_odi_issuer_sell_quantity": str(int(totals["odi_sell_quantity"])),
            "unmatched_buy_quantity": str(int(totals["unmatched_buy_quantity"])),
            "unmatched_sell_quantity": str(int(totals["unmatched_sell_quantity"])),
            "temporally_verified_fpi_buy_quantity": str(int(totals["verified_fpi_buy_quantity"])),
            "temporally_verified_fpi_sell_quantity": str(int(totals["verified_fpi_sell_quantity"])),
            "temporally_unverified_fpi_buy_quantity": str(int(totals["unverified_fpi_buy_quantity"])),
            "temporally_unverified_fpi_sell_quantity": str(int(totals["unverified_fpi_sell_quantity"])),
            "counterparty_identity_coverage_pct": (
                round(identified / total_events * 100, 2) if total_events else ""
            ),
            "known_fpi_net_status": net_status,
            "registry_temporal_status": ";".join(sorted({
                item["temporal_status"] for item in items
                if item["counterparty_class"] == "current_registry_matched_fpi_client"
            })) or "no_current_registry_matched_fpi_clients",
        })
    return results


def classify(
    root: Path = Path("."),
    lookback_days: int = 7,
    from_value: str | None = None,
    to_value: str | None = None,
) -> dict:
    if from_value is None and to_value is None:
        if lookback_days < 1 or lookback_days > 60:
            raise ValueError("lookback_days must be between 1 and 60 when using a rolling window")
        requested_start = requested_end = None
    else:
        if not from_value or not to_value:
            raise ValueError("explicit deal window requires both --from-date and --to-date")
        requested_start = datetime.strptime(from_value, "%d-%m-%Y").date()
        requested_end = datetime.strptime(to_value, "%d-%m-%Y").date()
        if requested_start > requested_end:
            raise ValueError("deal from-date must not be after to-date")
        if (requested_end - requested_start).days > 365:
            raise ValueError("explicit deal window must not exceed 365 calendar days")
    registry_manifest, registry_index = _load_registry(root)
    registry_snapshot_date = date.fromisoformat(registry_manifest["retrieved_date_ist"])
    (
        universe_by_isin, universe_by_symbol, universe_by_company_name,
        universe_rows, universe_metadata
    ) = _load_universe(root)
    selected_manifests, start_date, end_date = _load_latest_deal_manifests(
        root, lookback_days, requested_start, requested_end
    )
    source_rows, source_refs = _load_deal_rows(root, selected_manifests)
    events = _deduplicate_cross_report_rows(source_rows)
    events = [
        _classify_event(
            event, registry_index, registry_snapshot_date,
            universe_by_isin, universe_by_symbol, universe_by_company_name,
        )
        for event in events
    ]
    for event in events:
        for field in EVENT_FIELDS:
            event.setdefault(field, "")
    stock_rows = _stock_summary(events, start_date, end_date, universe_rows)
    event_payload = _csv_bytes(events, EVENT_FIELDS)
    stock_payload = _csv_bytes(stock_rows, STOCK_FIELDS)
    event_sha = _sha256(event_payload)
    stock_sha = _sha256(stock_payload)
    snapshot_hash = _sha256((event_sha + stock_sha).encode())
    stem = f"fpi_deal_attribution_{end_date:%Y%m%d}_{snapshot_hash[:16]}"
    event_snapshot_rel = f"data/snapshots/fpi_deal_attribution/{stem}_events.csv"
    stock_snapshot_rel = f"data/snapshots/fpi_deal_attribution/{stem}_stocks.csv"
    snapshot_manifest_rel = f"data/snapshots/fpi_deal_attribution/{stem}.json"

    snapshot_manifest = {
        "dataset": "nse_bulk_block_deal_fpi_counterparty_classification",
        "parser_version": PARSER_VERSION,
        "status": "complete" if events else "complete_no_deals",
        "lookback_calendar_days": (end_date - start_date).days,
        "trade_date_window_start": start_date.isoformat(),
        "trade_date_window_end": end_date.isoformat(),
        "registry_snapshot_date": registry_snapshot_date.isoformat(),
        "registry_snapshot_path": registry_manifest["normalized_csv_path"],
        "registry_snapshot_sha256": registry_manifest["normalized_csv_sha256"],
        "universe_source_path": universe_metadata["path"],
        "universe_csv_sha256": universe_metadata["sha256"],
        "universe_join_policy": (
            "NSE bulk/block disclosure has no ISIN field in the validated schema; "
            "membership in the latest published Nifty 500 snapshot therefore requires exact "
            "symbol plus exact normalized company-name match. The universe ISIN is attached "
            "from the official constituent file."
        ),
        "universe_snapshot_manifest_path": universe_metadata["snapshot_manifest_path"],
        "universe_snapshot_retrieved_at_utc": universe_metadata["retrieved_at_utc"],
        "universe_snapshot_freshness_statement": universe_metadata["source_freshness_statement"],
        "universe_effective_date_status": universe_metadata["source_freshness_semantics"],
        "universe_source_publisher": universe_metadata["publisher"],
        "universe_source_page": universe_metadata["source_page"],
        "universe_source_download_url": universe_metadata["source_download_url"],
        "universe_row_count": universe_metadata["row_count"],
        "nifty500_snapshot_verified_event_count": sum(
            event["nifty500_snapshot_member_verified"] == "true" for event in events
        ),
        "source_isin_supplied_event_count": sum(
            bool(event["source_isin"]) for event in events
        ),
        "universe_join_method_counts": {
            method: sum(event["universe_join_method"] == method for event in events)
            for method in sorted({event["universe_join_method"] for event in events})
        },
        "nifty500_snapshot_member_count": len(stock_rows),
        "nifty500_snapshot_verified_fpi_client_event_count": sum(
            event["nifty500_snapshot_member_verified"] == "true"
            and event["counterparty_class"] == "current_registry_matched_fpi_client"
            for event in events
        ),
        "nifty500_snapshot_verified_odi_issuer_event_count": sum(
            event["nifty500_snapshot_member_verified"] == "true"
            and event["counterparty_class"] == "current_registry_matched_odi_issuer"
            for event in events
        ),
        "outside_or_unverified_universe_event_count": sum(
            event["nifty500_snapshot_member_verified"] != "true" for event in events
        ),
        "stock_summary_row_count": len(stock_rows),
        "nifty500_symbols_with_verified_deal_events": sum(
            row["deal_data_status"] == "observed_disclosed_deals" for row in stock_rows
        ),
        "nifty500_symbols_with_unverified_deal_records": sum(
            row["deal_data_status"] == "deal_records_present_but_universe_join_unverified"
            or int(row["unverified_deal_event_count"]) > 0
            for row in stock_rows
        ),
        "nifty500_symbols_without_disclosed_deals": sum(
            row["deal_data_status"] == "no_disclosed_deal_in_available_snapshots"
            for row in stock_rows
        ),
        "nifty500_symbols_with_direct_fpi_client_events": sum(
            int(row["current_registry_matched_fpi_client_event_count"]) > 0
            for row in stock_rows
        ),
        "source_row_count": len(source_rows),
        "deduplicated_event_count": len(events),
        "cross_report_duplicate_event_count": sum(
            event["cross_report_duplicate"] == "true" for event in events
        ),
        "current_registry_matched_fpi_client_event_count": sum(
            event["counterparty_class"] == "current_registry_matched_fpi_client" for event in events
        ),
        "current_registry_matched_odi_issuer_event_count": sum(
            event["counterparty_class"] == "current_registry_matched_odi_issuer" for event in events
        ),
        "ambiguous_registration_event_count": sum(
            event["counterparty_class"] == "current_registry_match_ambiguous_registration"
            for event in events
        ),
        "unmatched_event_count": sum(
            event["counterparty_class"] == "unmatched" for event in events
        ),
        "event_csv_sha256": event_sha,
        "stock_summary_csv_sha256": stock_sha,
        "event_snapshot_path": event_snapshot_rel,
        "stock_summary_snapshot_path": stock_snapshot_rel,
        "snapshot_manifest_path": snapshot_manifest_rel,
        "source_deal_manifests": source_refs,
        "temporal_boundary": (
            "A current registry match does not prove FPI registration on an earlier trade date "
            "because the CDSL directory does not publish registration start dates. Same-day "
            "registry presence is reported separately. ODI issuers are not counted as beneficial "
            "FPI investors; unmatched counterparties remain unresolved, not non-FPI."
        ),
    }

    root_out = root
    _write_immutable(root_out / event_snapshot_rel, event_payload)
    _write_immutable(root_out / stock_snapshot_rel, stock_payload)
    _write_immutable(
        root_out / snapshot_manifest_rel,
        (json.dumps(snapshot_manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
    )
    for key, payload in (("events", event_payload), ("stocks", stock_payload)):
        current_path = root_out / OUTPUT_PATHS[key]
        current_path.parent.mkdir(parents=True, exist_ok=True)
        current_path.write_bytes(payload)
    current_manifest = {
        **snapshot_manifest,
        "current_event_path": OUTPUT_PATHS["events"],
        "current_stock_summary_path": OUTPUT_PATHS["stocks"],
        "current_manifest_path": OUTPUT_PATHS["manifest"],
    }
    current_manifest_path = root_out / OUTPUT_PATHS["manifest"]
    current_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    current_manifest_path.write_text(
        json.dumps(current_manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return current_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--from-date", type=str, default=None, help="DD-MM-YYYY")
    parser.add_argument("--to-date", type=str, default=None, help="DD-MM-YYYY")
    args = parser.parse_args()
    print(json.dumps(
        classify(args.root, args.lookback_days, args.from_date, args.to_date),
        indent=2,
    ))


if __name__ == "__main__":
    main()
