"""Blend official FPI deal, ownership, cash, delivery and derivatives evidence without premature scoring."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

UNIVERSE_CSV = Path("data/raw/universe/nifty500_current.csv")
UNIVERSE_MANIFEST_DIR = Path("data/snapshots/universe")
DAILY_MANIFEST = Path("data/raw/daily_stock_features/daily_stock_features_current_manifest.json")
DAILY_CSV = Path("data/raw/daily_stock_features/daily_stock_features_current.csv")
HISTORICAL_MANIFEST = Path("data/raw/historical_features/historical_features_current_manifest.json")
HISTORICAL_CSV = Path("data/raw/historical_features/stock_historical_features_current.csv")
MARKET_FLOW_CSV = Path("data/raw/historical_features/market_flow_history_current.csv")
PARTICIPANT_CSV = Path("data/raw/historical_features/participant_derivatives_history_current.csv")
DEAL_MANIFEST = Path("data/raw/fpi_deal_attribution/fpi_deal_attribution_current_manifest.json")
DEAL_CSV = Path("data/raw/fpi_deal_attribution/fpi_deal_stock_summary_current.csv")
SHAREHOLDING_MANIFEST = Path("data/raw/shareholding/current_manifest.json")
SHAREHOLDING_CSV = Path("data/raw/shareholding/shareholding_ownership_current.csv")
OUTPUT_CSV = Path("data/raw/fpi_evidence_blend/fpi_evidence_matrix_current.csv")
OUTPUT_MANIFEST = Path("data/raw/fpi_evidence_blend/fpi_evidence_matrix_current_manifest.json")
PARSER_VERSION = 1

DAILY_FIELDS = [
    "trade_date", "fno_status", "cash_data_status", "cash_close",
    "cash_previous_close", "cash_return_pct", "cash_volume", "cash_turnover",
    "cash_trade_count", "delivery_status", "delivery_quantity",
    "delivery_percentage", "futures_data_status", "futures_expiry",
    "futures_close", "futures_return_pct", "futures_open_interest",
    "futures_oi_change", "futures_oi_change_pct", "futures_volume",
    "futures_buildup", "options_data_status", "options_expiry",
    "options_pcr_oi", "options_pcr_volume",
]
HISTORICAL_METRICS = [
    "status", "sessions_available", "stock_observations", "cash_return_pct",
    "cash_return_status", "average_daily_volume", "volume_status",
    "average_delivery_percentage", "delivery_status", "futures_observations",
    "futures_buildup_counts", "futures_buildup_status",
    "average_options_pcr_oi_same_expiry", "options_pcr_status",
]
SHAREHOLDING_FIELDS = [
    "reporting_date", "submission_date", "broadcast_date", "system_date",
    "record_id", "revised_data", "revision_date", "identity_status",
    "identity_join_method", "filing_status", "fpi_category_i_shares",
    "fpi_category_ii_shares", "fpi_total_shares",
    "fpi_category_i_ownership_percentage_points",
    "fpi_category_ii_ownership_percentage_points",
    "fpi_total_ownership_percentage_points", "other_foreign_institution_shares",
    "overseas_depositories_shares", "foreign_direct_investment_shares",
    "foreign_venture_capital_investor_shares",
    "sovereign_wealth_funds_foreign_shares", "foreign_institutions_total_shares",
    "fpi_reconciliation_status",
]
DEAL_FIELDS = [
    "deal_data_status", "deal_event_count", "unverified_deal_event_count",
    "total_deal_event_count", "source_record_count",
    "unverified_source_record_count", "cross_report_duplicate_event_count",
    "current_registry_matched_fpi_client_event_count",
    "current_registry_matched_odi_issuer_event_count",
    "ambiguous_registration_event_count", "unmatched_event_count",
    "current_registry_matched_fpi_client_buy_quantity",
    "current_registry_matched_fpi_client_sell_quantity",
    "current_registry_matched_fpi_client_known_net_quantity",
    "current_registry_matched_fpi_client_buy_value",
    "current_registry_matched_fpi_client_sell_value",
    "current_registry_matched_odi_issuer_buy_quantity",
    "current_registry_matched_odi_issuer_sell_quantity",
    "unmatched_buy_quantity", "unmatched_sell_quantity",
    "temporally_verified_fpi_buy_quantity", "temporally_verified_fpi_sell_quantity",
    "temporally_unverified_fpi_buy_quantity", "temporally_unverified_fpi_sell_quantity",
    "counterparty_identity_coverage_pct", "known_fpi_net_status",
    "registry_temporal_status",
]

OUTPUT_FIELDS = [
    "as_of_trade_date", "symbol", "company_name", "industry", "series", "isin",
    "daily_join_status",
    *[f"daily_{field}" for field in DAILY_FIELDS],
    "historical_join_status",
    *[f"{window}s_{field}" for window in (5, 20, 60) for field in HISTORICAL_METRICS],
    "shareholding_join_status",
    *[f"shareholding_{field}" for field in SHAREHOLDING_FIELDS],
    "deal_join_status",
    *[f"deal_{field}" for field in DEAL_FIELDS],
    "direct_fpi_client_gross_buy_candidate",
    "direct_fpi_client_known_net_buy_positive",
    "fpi_deal_evidence_grade",
    "deal_evidence_pattern",
    "short_term_price_context",
    "positional_price_context",
    "delivery_context",
    "options_context",
    "ownership_context",
    "evidence_summary",
    "composite_score_status",
]


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _load_json(root: Path, relative_path: Path) -> dict:
    return json.loads((root / relative_path).read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"CSV header missing: {path}")
        return list(reader)


def _csv_bytes(rows: list[dict], fields: list[str]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _verify_hash(root: Path, relative_path: str | Path, expected_sha: str, label: str) -> Path:
    path = root / relative_path
    payload = path.read_bytes()
    actual = _sha256(payload)
    if actual != expected_sha:
        raise ValueError(f"{label} SHA-256 mismatch: expected {expected_sha}, got {actual}")
    return path


def _unique_index(rows: list[dict[str, str]], key: str, label: str) -> dict[str, dict]:
    values = [row.get(key, "").strip().upper() for row in rows]
    if any(not value for value in values):
        raise ValueError(f"{label} contains blank {key}")
    if len(set(values)) != len(values):
        raise ValueError(f"{label} contains duplicate {key}")
    return {row[key].strip().upper(): row for row in rows}


def _load_universe(root: Path) -> tuple[list[dict], dict]:
    path = root / UNIVERSE_CSV
    payload = path.read_bytes()
    rows = _read_csv(path)
    if not 480 <= len(rows) <= 520:
        raise ValueError(f"unexpected Nifty 500 row count: {len(rows)}")
    required = {"symbol", "company_name", "industry", "series", "isin"}
    if not required.issubset(rows[0]):
        raise ValueError("Nifty 500 constituent schema incomplete")
    _unique_index(rows, "symbol", "Nifty 500 universe")
    _unique_index(rows, "isin", "Nifty 500 universe")
    digest = _sha256(payload)
    matching = []
    for manifest_path in sorted((root / UNIVERSE_MANIFEST_DIR).glob("nifty500_*.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("normalized_sha256") == digest:
            matching.append((manifest_path, manifest))
    if len(matching) != 1:
        raise ValueError("Nifty 500 CSV must match exactly one immutable snapshot manifest")
    manifest_path, manifest = matching[0]
    return rows, {
        "path": str(UNIVERSE_CSV),
        "sha256": digest,
        "row_count": len(rows),
        "snapshot_manifest_path": str(manifest_path.relative_to(root)),
        "retrieved_at_utc": manifest["retrieved_at_utc"],
        "freshness_statement": manifest["source_freshness_statement"],
        "freshness_semantics": manifest["source_freshness_semantics"],
        "publisher": manifest["publisher"],
        "source_page": manifest["source_page"],
    }


def _direction(value: str, status: str) -> str:
    if status != "complete" or value in (None, ""):
        return "unavailable"
    number = float(value)
    return "positive" if number > 0 else "negative" if number < 0 else "flat"


def _deal_pattern(row: dict) -> str:
    if row["deal_data_status"] == "no_disclosed_deal_in_available_snapshots":
        return "no_named_bulk_block_deal_in_window"
    if row["deal_data_status"] == "deal_records_present_but_universe_join_unverified":
        return "deal_records_present_universe_join_unverified"
    buy = int(row["current_registry_matched_fpi_client_buy_quantity"] or 0)
    sell = int(row["current_registry_matched_fpi_client_sell_quantity"] or 0)
    net = int(row["current_registry_matched_fpi_client_known_net_quantity"] or 0)
    if buy and sell and net == 0:
        return "matched_fpi_client_buys_and_sells_balance"
    if buy and net > 0:
        return (
            "matched_fpi_client_known_net_buy_partial"
            if row["known_fpi_net_status"] != "complete_for_current_registry_matched_counterparties"
            else "matched_fpi_client_known_net_buy_complete"
        )
    if sell and net < 0:
        return "matched_fpi_client_known_net_sell"
    if buy:
        return "matched_fpi_client_buys_no_matched_sells"
    if int(row["current_registry_matched_odi_issuer_event_count"] or 0):
        return "odi_issuer_activity_not_beneficial_fpi_attribution"
    if int(row["ambiguous_registration_event_count"] or 0):
        return "ambiguous_fpi_registry_match_present"
    return "disclosed_deals_without_direct_fpi_client_match"


def _price_context(row: dict, window: str) -> str:
    status = row.get(f"{window}s_cash_return_status", "")
    direction = _direction(row.get(f"{window}s_cash_return_pct", ""), status)
    return f"{window}s_{direction}_price_return"


def _positional_context(row: dict) -> str:
    twenty = _direction(row.get("20s_cash_return_pct", ""), row.get("20s_cash_return_status", ""))
    sixty = _direction(row.get("60s_cash_return_pct", ""), row.get("60s_cash_return_status", ""))
    if "unavailable" in (twenty, sixty):
        return "unavailable"
    if twenty == "positive" and sixty == "positive":
        return "positive_20s_and_60s"
    if twenty == "positive" and sixty == "negative":
        return "20s_positive_60s_negative"
    if twenty == "negative" and sixty == "negative":
        return "negative_20s_and_60s"
    if twenty == "negative" and sixty == "positive":
        return "20s_negative_60s_positive"
    return "mixed_or_flat"


def _delivery_context(row: dict) -> str:
    five_status = row.get("5s_delivery_status", "")
    twenty_status = row.get("20s_delivery_status", "")
    five = row.get("5s_average_delivery_percentage", "")
    twenty = row.get("20s_average_delivery_percentage", "")
    if five_status != "complete" or twenty_status != "complete" or not five or not twenty:
        return "unavailable"
    delta = float(five) - float(twenty)
    if delta >= 5:
        return "5s_delivery_strengthening_vs_20s"
    if delta >= 0:
        return "5s_delivery_at_or_above_20s"
    return "5s_delivery_below_20s"


def _options_context(row: dict) -> str:
    status = row.get("5s_options_pcr_status", "")
    if status == "complete_same_expiry":
        return "5s_same_expiry_pcr_available;longer_windows_withheld_if_expiry_roll"
    if status:
        return f"5s_options_pcr_{status}"
    return "options_pcr_unavailable"


def _ownership_context(row: dict) -> str:
    if row.get("shareholding_join_status") == "parsed_verified_identity":
        return "2026_quarterly_fpi_ownership_baseline_available;no_prior_quarter_change_series"
    return "quarterly_fpi_ownership_baseline_unavailable_or_unverified"


def _load_source(root: Path, manifest_path: Path, csv_path: Path, hash_key: str) -> tuple[dict, list[dict]]:
    manifest = _load_json(root, manifest_path)
    expected = manifest[hash_key]
    path = _verify_hash(root, csv_path, expected, str(csv_path))
    return manifest, _read_csv(path)


def build(root: Path = Path(".")) -> dict:
    universe_rows, universe_meta = _load_universe(root)
    daily_manifest, daily_rows = _load_source(
        root, DAILY_MANIFEST, DAILY_CSV, "output_sha256"
    )
    historical_manifest, historical_rows = _load_source(
        root, HISTORICAL_MANIFEST, HISTORICAL_CSV, "stock_output_sha256"
    )
    deal_manifest, deal_rows = _load_source(
        root, DEAL_MANIFEST, DEAL_CSV, "stock_summary_csv_sha256"
    )
    share_manifest, share_rows = _load_source(
        root, SHAREHOLDING_MANIFEST, SHAREHOLDING_CSV, "ownership_current_sha256"
    )
    market_flow_path = _verify_hash(
        root, MARKET_FLOW_CSV, historical_manifest["market_output_sha256"], "market flow history"
    )
    participant_path = _verify_hash(
        root, PARTICIPANT_CSV, historical_manifest["participant_output_sha256"],
        "participant derivatives history",
    )
    market_rows = _read_csv(market_flow_path)
    participant_rows = _read_csv(participant_path)

    daily_by_symbol = _unique_index(daily_rows, "symbol", "daily stock features")
    historical_by_symbol = _unique_index(historical_rows, "symbol", "historical stock features")
    deal_by_symbol = _unique_index(deal_rows, "symbol", "FPI deal stock summary")
    share_by_symbol = _unique_index(share_rows, "symbol", "shareholding ownership")

    expected_count = len(universe_rows)
    for label, rows in (
        ("daily", daily_rows), ("historical", historical_rows),
        ("deal", deal_rows), ("shareholding", share_rows),
    ):
        if len(rows) != expected_count:
            raise ValueError(f"{label} row count {len(rows)} does not match universe {expected_count}")
    if share_manifest.get("universe_sha256") != universe_meta["sha256"]:
        raise ValueError("shareholding universe hash does not match current Nifty 500 snapshot")
    if deal_manifest.get("universe_csv_sha256") != universe_meta["sha256"]:
        raise ValueError("FPI deal universe hash does not match current Nifty 500 snapshot")
    as_of_trade_date = daily_manifest["trade_date"]
    if historical_manifest["as_of_trade_date"] != as_of_trade_date:
        raise ValueError("daily and historical stock features have different as-of trade dates")

    output_rows = []
    daily_join_counts = Counter()
    historical_join_counts = Counter()
    ownership_status_counts = Counter()
    deal_status_counts = Counter()
    for universe in sorted(universe_rows, key=lambda row: row["symbol"]):
        symbol = universe["symbol"].strip().upper()
        isin = universe["isin"].strip().upper()
        daily = daily_by_symbol[symbol]
        historical = historical_by_symbol[symbol]
        deal = deal_by_symbol[symbol]
        share = share_by_symbol[symbol]

        daily_status = "isin_exact" if daily.get("isin", "").strip().upper() == isin else "isin_mismatch"
        historical_status = (
            "isin_exact" if historical.get("isin", "").strip().upper() == isin
            else "isin_mismatch"
        )
        deal_status = (
            "universe_isin_exact" if deal.get("universe_isin", "").strip().upper() == isin
            else "universe_isin_mismatch"
        )
        share_identity = share.get("identity_status", "")
        if share_identity == "verified" and share.get("isin", "").strip().upper() == isin:
            if share.get("filing_status") == "parsed":
                share_status = "parsed_verified_identity"
            else:
                share_status = "verified_identity_filing_not_parsed"
        elif share_identity == "isin_mismatch":
            share_status = "isin_mismatch"
        elif share_identity == "identity_unverified":
            share_status = "identity_unverified"
        elif share_identity == "not_found_in_current_master":
            share_status = "not_found_in_current_master"
        else:
            share_status = "ownership_identity_or_join_unavailable"

        daily_join_counts[daily_status] += 1
        historical_join_counts[historical_status] += 1
        ownership_status_counts[share_status] += 1
        deal_status_counts[deal.get("deal_data_status", "unknown")] += 1

        row = {
            "as_of_trade_date": as_of_trade_date,
            "symbol": symbol,
            "company_name": universe["company_name"],
            "industry": universe["industry"],
            "series": universe["series"],
            "isin": isin,
            "daily_join_status": daily_status,
            "historical_join_status": historical_status,
            "shareholding_join_status": share_status,
            "deal_join_status": deal_status,
        }

        for field in DAILY_FIELDS:
            row[f"daily_{field}"] = daily.get(field, "") if daily_status == "isin_exact" else ""
        for window in (5, 20, 60):
            for field in HISTORICAL_METRICS:
                key = f"{window}s_{field}"
                row[key] = historical.get(key, "") if historical_status == "isin_exact" else ""
        for field in SHAREHOLDING_FIELDS:
            value = share.get(field, "")
            if share_status != "parsed_verified_identity" and field in {
                "fpi_category_i_shares", "fpi_category_ii_shares", "fpi_total_shares",
                "fpi_category_i_ownership_percentage_points",
                "fpi_category_ii_ownership_percentage_points",
                "fpi_total_ownership_percentage_points",
            }:
                value = ""
            row[f"shareholding_{field}"] = value
        for field in DEAL_FIELDS:
            row[f"deal_{field}"] = deal.get(field, "") if deal_status == "universe_isin_exact" else ""

        buy = int(row.get("deal_current_registry_matched_fpi_client_buy_quantity") or 0)
        sell = int(row.get("deal_current_registry_matched_fpi_client_sell_quantity") or 0)
        net = int(row.get("deal_current_registry_matched_fpi_client_known_net_quantity") or 0)
        deal_data_status = row.get("deal_deal_data_status", "")
        net_status = row.get("deal_known_fpi_net_status", "")
        temporal = row.get("deal_registry_temporal_status", "")
        direct_buy = buy > 0 and deal_status == "universe_isin_exact"
        row["direct_fpi_client_gross_buy_candidate"] = str(direct_buy).lower()
        row["direct_fpi_client_known_net_buy_positive"] = str(
            net > 0 and deal_status == "universe_isin_exact"
        ).lower()
        if direct_buy and "observed_in_registry_on_trade_date" in temporal:
            grade = "grade_1_same_day_registry_evidence"
        elif direct_buy:
            grade = "grade_2_named_client_current_registry_match_temporal_status_unverified"
        else:
            grade = "not_assigned_no_direct_fpi_client_buy_evidence"
        row["fpi_deal_evidence_grade"] = grade
        row["deal_evidence_pattern"] = (
            _deal_pattern(deal) if deal_status == "universe_isin_exact"
            else "deal_summary_universe_isin_mismatch"
        )
        row["short_term_price_context"] = _price_context(row, 5) if historical_status == "isin_exact" else "unavailable"
        row["positional_price_context"] = _positional_context(row) if historical_status == "isin_exact" else "unavailable"
        row["delivery_context"] = _delivery_context(row) if historical_status == "isin_exact" else "unavailable"
        row["options_context"] = _options_context(row) if historical_status == "isin_exact" else "unavailable"
        row["ownership_context"] = _ownership_context(row)
        summary_parts = []
        if direct_buy:
            summary_parts.append("direct_named_fpi_client_buy_disclosed")
        if direct_buy and net_status != "complete_for_current_registry_matched_counterparties":
            summary_parts.append("known_net_incomplete_or_counterparty_unresolved")
        if deal_data_status == "no_disclosed_deal_in_available_snapshots":
            summary_parts.append("no_named_bulk_block_deal_in_window")
        if share_status == "parsed_verified_identity":
            summary_parts.append("quarterly_fpi_ownership_baseline_available")
        if row["short_term_price_context"] == "5s_positive_price_return":
            summary_parts.append("positive_5_session_price_return")
        if row["positional_price_context"] in {
            "negative_20s_and_60s", "20s_positive_60s_negative", "20s_negative_60s_positive"
        }:
            summary_parts.append("mixed_or_weak_positional_price_trend")
        if row["delivery_context"] == "5s_delivery_strengthening_vs_20s":
            summary_parts.append("delivery_strengthening_vs_20s")
        if row["options_context"].startswith("5s_same_expiry_pcr_available"):
            summary_parts.append("5_session_options_pcr_same_expiry_available")
        if row["ownership_context"] != "2026_quarterly_fpi_ownership_baseline_available;no_prior_quarter_change_series":
            summary_parts.append("quarterly_fpi_ownership_baseline_unavailable_or_unverified")
        if deal_data_status == "observed_disclosed_deals" and not direct_buy:
            summary_parts.append("bulk_block_deals_disclosed_without_direct_fpi_client_buy_match")
        row["evidence_summary"] = ";".join(summary_parts) if summary_parts else "no_positive_stock_specific_fpi_deal_evidence_in_available_sources"
        row["composite_score_status"] = "not_scored_independent_evidence_dimensions_only"
        output_rows.append(row)

    if len(output_rows) != expected_count:
        raise ValueError("blended evidence matrix does not cover the full Nifty 500 universe")

    market_statuses = [
        {
            "category": row["category"],
            "window_sessions": int(row["window_sessions"]),
            "sessions_available": int(row["sessions_available"]),
            "status": row["status"],
        }
        for row in market_rows
    ]
    participant_statuses = [
        {
            "dataset": row["dataset"],
            "participant_category": row["participant_category"],
            "instrument_group": row["instrument_group"],
            "option_type": row["option_type"],
            "source_side": row["source_side"],
            "window_sessions": int(row["window_sessions"]),
            "sessions_available": int(row["sessions_available"]),
            "scope": row["scope"],
            "status": row["status"],
        }
        for row in participant_rows
    ]
    output_payload = _csv_bytes(output_rows, OUTPUT_FIELDS)
    output_sha = _sha256(output_payload)
    as_of = as_of_trade_date.replace("-", "")
    stem = f"fpi_evidence_matrix_{as_of}_{output_sha[:16]}"
    snapshot_path = Path("data/snapshots/fpi_evidence_blend") / f"{stem}.csv"
    snapshot_manifest_path = Path("data/snapshots/fpi_evidence_blend") / f"{stem}.json"
    snapshot_manifest = {
        "dataset": "nifty500_blended_fpi_accumulation_evidence_matrix",
        "parser_version": PARSER_VERSION,
        "status": "complete",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "as_of_trade_date": as_of_trade_date,
        "stock_count": len(output_rows),
        "direct_fpi_client_gross_buy_candidate_count": sum(
            row["direct_fpi_client_gross_buy_candidate"] == "true" for row in output_rows
        ),
        "grade_1_count": sum(row["fpi_deal_evidence_grade"].startswith("grade_1") for row in output_rows),
        "grade_2_count": sum(row["fpi_deal_evidence_grade"].startswith("grade_2") for row in output_rows),
        "ownership_reporting_date": share_manifest.get("latest_reporting_date"),
        "ownership_parsed_verified_count": ownership_status_counts["parsed_verified_identity"],
        "ownership_coverage_pct": round(
            ownership_status_counts["parsed_verified_identity"] / len(output_rows) * 100, 2
        ),
        "ownership_status_counts": dict(ownership_status_counts),
        "daily_join_status_counts": dict(daily_join_counts),
        "historical_join_status_counts": dict(historical_join_counts),
        "deal_status_counts": dict(deal_status_counts),
        "deal_trade_date_window": [
            deal_manifest["trade_date_window_start"], deal_manifest["trade_date_window_end"]
        ],
        "registry_snapshot_date": deal_manifest["registry_snapshot_date"],
        "registry_temporal_boundary": (
            "CDSL registry snapshot is after the deal window; registration start dates are not "
            "published, so earlier trade-date registration is unverified."
        ),
        "universe_snapshot_manifest_path": universe_meta["snapshot_manifest_path"],
        "universe_snapshot_retrieved_at_utc": universe_meta["retrieved_at_utc"],
        "universe_snapshot_freshness_statement": universe_meta["freshness_statement"],
        "universe_effective_date_status": universe_meta["freshness_semantics"],
        "daily_stock_history_sessions": historical_manifest["daily_stock_session_count"],
        "daily_stock_history_as_of": historical_manifest["as_of_trade_date"],
        "market_flow_session_count": historical_manifest["market_flow_session_count"],
        "market_flow_window_statuses": market_statuses,
        "participant_oi_session_count": historical_manifest["participant_oi_session_count"],
        "participant_volume_session_count": historical_manifest["participant_volume_session_count"],
        "participant_derivatives_window_statuses": participant_statuses,
        "composite_score_status": "not_scored_independent_evidence_dimensions_only",
        "source_hashes": {
            "universe": universe_meta["sha256"],
            "daily_stock_features": daily_manifest["output_sha256"],
            "historical_stock_features": historical_manifest["stock_output_sha256"],
            "fpi_deal_stock_summary": deal_manifest["stock_summary_csv_sha256"],
            "shareholding_ownership": share_manifest["ownership_current_sha256"],
            "market_flow_history": historical_manifest["market_output_sha256"],
            "participant_derivatives_history": historical_manifest["participant_output_sha256"],
        },
        "output_csv_sha256": output_sha,
        "snapshot_path": str(snapshot_path),
        "snapshot_manifest_path": str(snapshot_manifest_path),
        "limitations": [
            "No composite score is issued; independent evidence dimensions are preserved without arbitrary weights.",
            "A named bulk/block client match is not complete FPI trading flow and does not establish beneficial ownership.",
            "The CDSL registry snapshot follows the deal window and does not publish registration start dates.",
            "The Nifty 500 source states data as of the last trading day of the previous month; exact effective date is not supplied.",
            "Quarterly FPI ownership has one reporting quarter only; no quarter-on-quarter change series is available yet.",
            "Shareholding identity coverage is partial; mismatched or unverified identities remain null.",
            "Market-wide FII/FPI and DII flows have only one session; 5/20/60-session baselines are insufficient.",
            "Participant OI and volume are category aggregates, not participant-by-stock positions, and currently have only one session.",
            "No disclosed bulk/block deal does not mean no FPI trading; it means no named transaction appeared in the collected reports.",
            "Futures buildup counts describe market-wide contract activity, not FII attribution; options PCR is retained only with same-expiry comparability."
        ],
    }
    root_out = root
    snapshot_path_abs = root_out / snapshot_path
    snapshot_path_abs.parent.mkdir(parents=True, exist_ok=True)
    if snapshot_path_abs.exists() and snapshot_path_abs.read_bytes() != output_payload:
        raise RuntimeError(f"immutable blend snapshot collision: {snapshot_path}")
    snapshot_path_abs.write_bytes(output_payload)
    snapshot_manifest_abs = root_out / snapshot_manifest_path
    snapshot_manifest_abs.write_text(
        json.dumps(snapshot_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    current_csv = root_out / OUTPUT_CSV
    current_csv.parent.mkdir(parents=True, exist_ok=True)
    current_csv.write_bytes(output_payload)
    current_manifest = {
        **snapshot_manifest,
        "current_output_path": str(OUTPUT_CSV),
        "current_manifest_path": str(OUTPUT_MANIFEST),
    }
    current_manifest_path = root_out / OUTPUT_MANIFEST
    current_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    current_manifest_path.write_text(
        json.dumps(current_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return current_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(build(args.root), indent=2))


if __name__ == "__main__":
    main()
