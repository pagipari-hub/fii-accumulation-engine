"""Build a conservative weekly FPI evidence watchlist from the validated blend."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _format_integer(row: dict[str, str], key: str) -> str:
    value = row.get(key, "").strip()
    return f"{int(float(value)):,}" if value else "N/A"


def _format_percent(row: dict[str, str], key: str) -> str:
    value = row.get(key, "").strip()
    return f"{float(value):.2f}%" if value else "N/A"


def _format_return(row: dict[str, str], key: str) -> str:
    value = row.get(key, "").strip()
    return f"{float(value):+.2f}%" if value else "N/A"


def _format_plain(value: str) -> str:
    descriptions = {
        "5s_negative_price_return": "Negative 5-session price return",
        "20s_positive_60s_negative": "Positive 20-session return but negative 60-session return",
        "5s_delivery_at_or_above_20s": "5-session average delivery is at or above the 20-session average",
        "5s_options_pcr_partial_observations": "Options PCR has partial observations",
        "2026_quarterly_fpi_ownership_baseline_available;no_prior_quarter_change_series": "Quarterly FPI ownership baseline available; no prior-quarter change series",
        "not_eligible": "Not eligible for F&O",
        "not_applicable": "Not applicable",
    }
    return descriptions.get(value, value.replace("_", " ") if value else "N/A")


def build_report(
    matrix_path: Path,
    manifest_path: Path,
    bse_fallback_manifest_path: Path,
    output_path: Path,
) -> dict:
    matrix_payload = matrix_path.read_bytes()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if _sha256(matrix_payload) != manifest.get("output_csv_sha256"):
        raise ValueError("FPI evidence matrix SHA-256 does not match its manifest")
    rows = _read_csv(matrix_path)
    if len(rows) != manifest.get("stock_count"):
        raise ValueError("FPI evidence matrix row count does not match manifest")
    if manifest.get("composite_score_status") != "not_scored_independent_evidence_dimensions_only":
        raise ValueError("watchlist must not publish an unapproved composite score")

    bse_payload = bse_fallback_manifest_path.read_bytes()
    bse_fallback = json.loads(bse_payload.decode("utf-8"))
    placeholders = [row for row in rows if row.get("universe_member_type") == "index_adjustment_placeholder"]
    tradable = [row for row in rows if row.get("fpi_scoring_eligible") == "true"]
    candidates = [
        row for row in tradable
        if row.get("direct_fpi_client_gross_buy_candidate") == "true"
    ]
    grade_1 = [row for row in candidates if row.get("fpi_deal_evidence_grade", "").startswith("grade_1")]
    grade_2 = [row for row in candidates if row.get("fpi_deal_evidence_grade", "").startswith("grade_2")]
    missing_ownership = sorted(
        row["symbol"] for row in tradable
        if row.get("shareholding_join_status") == "not_found_in_current_master"
    )
    ownership_status = manifest.get("ownership_status_counts", {})
    crosswalk = manifest.get("source_hashes", {}).get("shareholding_identity_crosswalk")
    nsdl = manifest.get("nsdl_market_flow_context")
    if nsdl and nsdl.get("stock_level_attribution") is not False:
        raise ValueError("NSDL aggregate context must not be attributed to individual stocks")
    if manifest.get("tradable_stock_count") != len(tradable):
        raise ValueError("tradable-stock count does not match matrix rows")
    parsed_count = manifest.get("ownership_parsed_verified_count", 0)
    expected_coverage = round(parsed_count / len(tradable) * 100, 2) if tradable else 0.0
    if manifest.get("ownership_coverage_pct") != expected_coverage:
        raise ValueError("ownership coverage percentage does not match tradable denominator")
    if manifest.get("index_placeholder_count") != len(placeholders):
        raise ValueError("index placeholder count does not match matrix rows")
    if len(candidates) != manifest.get("direct_fpi_client_gross_buy_candidate_count"):
        raise ValueError("direct FPI candidate count does not match matrix rows")

    lines = [
        "# Weekly FPI evidence watchlist",
        "",
        f"- **Market data as of:** {manifest.get('as_of_trade_date', 'N/A')}",
        f"- **Index rows:** {len(rows)}",
        f"- **Tradable securities:** {len(tradable)}",
        f"- **Non-tradable index placeholders:** {len(placeholders)}",
        "",
        "## Coverage and data quality",
        "",
        f"- **Quarterly ownership parsed and identity-verified:** {manifest.get('ownership_parsed_verified_count', 0)} / {len(tradable)} ({manifest.get('ownership_coverage_pct', 0):.1f}%).",
        f"- **Coverage across all index rows:** {manifest.get('ownership_index_row_coverage_pct', 0):.1f}%.",
        f"- **Parsed verified ownership rows:** {ownership_status.get('parsed_verified_identity', 0)}.",
        f"- **Dated BSE identity crosswalk:** {manifest.get('identity_crosswalk_verified_count', 0)} / {manifest.get('identity_crosswalk_candidate_count', 0)} verified; {manifest.get('identity_crosswalk_unresolved_count', 0)} unresolved.",
        f"- **BSE UDiFF source SHA-256:** {manifest.get('identity_crosswalk_bse_source_sha256', 'N/A')}.",
        f"- **Ownership rows not found in the current NSE filing master:** {len(missing_ownership)}.",
        "",
    ]
    if missing_ownership:
        lines.append("Missing current NSE ownership records: " + ", ".join(missing_ownership) + ".")
        lines.append("These remain N/A; no ownership values are imputed.")
        lines.append("")

    lines.extend([
        "## Stock-specific FPI evidence",
        "",
        f"- **Grade 1 candidates:** {len(grade_1)}",
        f"- **Grade 2 candidates:** {len(grade_2)}",
        f"- **Named FPI-client gross-buy candidates:** {len(candidates)}",
        f"- **Other tradable securities without a qualifying named-client gross-buy candidate:** {len(tradable) - len(candidates)}.",
        "",
    ])
    if candidates:
        lines.extend([
            "| Security | Evidence grade | Deal evidence pattern | Registry timing | Known net status |",
            "|---|---:|---|---|---|",
        ])
        for row in sorted(candidates, key=lambda item: (item.get("fpi_deal_evidence_grade", ""), item.get("symbol", ""))):
            lines.append(
                f"| {row['symbol']} — {row['company_name']} | "
                f"{row.get('fpi_deal_evidence_grade', 'N/A')} | "
                f"{row.get('deal_evidence_pattern', 'N/A')} | "
                f"{row.get('deal_registry_temporal_status', 'N/A')} | "
                f"{row.get('deal_known_fpi_net_status', 'N/A')} |"
            )
        lines.append("")
        for row in candidates:
            lines.extend([
                f"### {row['company_name']} ({row['symbol']})",
                "",
                f"- **Current-registry-matched FPI client gross buys / sells:** {_format_integer(row, 'deal_current_registry_matched_fpi_client_buy_quantity')} / {_format_integer(row, 'deal_current_registry_matched_fpi_client_sell_quantity')} shares.",
                f"- **Unmatched gross sells:** {_format_integer(row, 'deal_unmatched_sell_quantity')} shares; counterparty identity coverage {_format_percent(row, 'deal_counterparty_identity_coverage_pct')}.",
                f"- **Registry timing:** {_format_plain(row.get('deal_registry_temporal_status', ''))}.",
                f"- **Known FPI net status:** {_format_plain(row.get('deal_known_fpi_net_status', ''))}.",
                f"- **Short term (5 sessions):** price return {_format_return(row, '5s_cash_return_pct')}; average delivery {_format_percent(row, '5s_average_delivery_percentage')}.",
                f"- **Positional (20 / 60 sessions):** price returns {_format_return(row, '20s_cash_return_pct')} / {_format_return(row, '60s_cash_return_pct')}; delivery context {_format_plain(row.get('delivery_context', ''))}.",
                f"- **Quarterly ownership:** FPI total {_format_percent(row, 'shareholding_fpi_total_ownership_percentage_points')} as of {row.get('shareholding_reporting_date', 'N/A')}; {_format_plain(row.get('ownership_context', ''))}.",
                f"- **Derivatives:** futures {_format_plain(row.get('daily_futures_data_status', ''))}; options {_format_plain(row.get('daily_options_data_status', ''))}; {_format_plain(row.get('options_context', ''))}.",
                "- **Why included:** a named current-registry FPI-client gross-buy disclosure is visible, but registration timing is not verified for the transaction date and sell-side counterparty identity is incomplete. This is a lead, not confirmed net accumulation.",
                "- **Catalyst:** No sourced business/corporate catalyst or buyer motive is attached; none is inferred.",
                "",
            ])
        lines.append(
            "A Grade 2 gross-buy match is a lead, not proof of net FPI accumulation. "
            "Unverified registration timing and incomplete buy/sell totals must remain explicit."
        )
    else:
        lines.append("No named FPI-client gross-buy candidates passed the current evidence gates.")
    lines.append("")

    lines.extend([
        "## Aggregate market-wide cash-flow context",
        "",
    ])
    if nsdl:
        fpi = nsdl.get("fpi", {})
        dii = nsdl.get("dii", {})
        lines.extend([
            f"- **NSDL observation window:** {nsdl.get('start_date')} to {nsdl.get('end_date')} ({nsdl.get('normalized_date_count')} dates).",
            f"- **FPI cash-equity gross purchases / sales:** ₹{fpi.get('gross_purchases_inr_crore', 0):,.2f} / ₹{fpi.get('gross_sales_inr_crore', 0):,.2f} crore.",
            f"- **FPI cash-equity net:** ₹{fpi.get('cumulative_net_inr_crore', 0):,.2f} crore across {fpi.get('observed_days', 0)} observations; {fpi.get('positive_net_days', 0)} positive and {fpi.get('negative_net_days', 0)} negative days.",
            f"- **DII cash-equity gross purchases / sales:** ₹{dii.get('gross_purchases_inr_crore', 0):,.2f} / ₹{dii.get('gross_sales_inr_crore', 0):,.2f} crore.",
            f"- **DII cash-equity net:** ₹{dii.get('cumulative_net_inr_crore', 0):,.2f} crore across {dii.get('observed_days', 0)} observations; {dii.get('positive_net_days', 0)} positive and {dii.get('negative_net_days', 0)} negative days.",
            f"- **Overlapping FPI/DII dates:** {nsdl.get('overlapping_fpi_dii_days', 0)}.",
            f"- **Explicit DII No Data dates:** {', '.join(nsdl.get('dii_no_data_days', [])) or 'None'}.",
            "- **Source construction:** NSDL FPI Equity Sub-total and explicit DII Equity Total; debt, derivatives and duplicate investor subtotals are excluded.",
            "These are aggregate market-wide flows only. They do not establish which individual stocks were bought or sold.",
            "",
        ])
    else:
        lines.extend([
            "NSDL aggregate cash-flow history has not been integrated into this matrix build.",
            "",
        ])

    history_probe = manifest.get("nse_shareholding_history_probe") or {}
    announcement_probe = manifest.get("nse_shareholding_unitholding_announcements_probe") or {}
    lines.extend([
        "## Historical ownership source discovery",
        "",
    ])
    if history_probe:
        market_history = history_probe.get("market_wide_history", {})
        lines.append(
            f"- **NSE market-wide shareholding master:** {market_history.get('row_count', 0):,} rows across {market_history.get('unique_symbol_count', 0):,} symbols ({history_probe.get('date_range', {}).get('from_date', 'N/A')} to {history_probe.get('date_range', {}).get('to_date', 'N/A')})."
        )
        lines.append(
            f"- **NSE symbol-specific master history rows:** {json.dumps(history_probe.get('row_counts', {}), sort_keys=True)}."
        )
        lines.append(
            f"- **Symbols returning multiple master records:** {', '.join(history_probe.get('symbols_with_multiple_records', [])) or 'None'}."
        )
    else:
        lines.append("NSE symbol-specific master history probe was not available in this build.")
    if announcement_probe:
        matches = announcement_probe.get("matching_announcement_counts", {})
        attachments = [
            attachment
            for observation in announcement_probe.get("observations", [])
            for attachment in observation.get("attachments", [])
        ]
        captured = sum(item.get("status") == "captured" for item in attachments)
        errors = sum(item.get("status") == "download_error" for item in attachments)
        lines.append(f"- **NSE shareholding/unitholding pattern announcements:** {json.dumps(matches, sort_keys=True)}.")
        lines.append(f"- **Official attachments captured / failed:** {captured} / {errors}.")
    else:
        lines.append("NSE shareholding/unitholding announcement probe was not available in this build.")
    lines.append(
        "These probes discover historical filing sources only. Their records are not used as ownership values until quarter dates, category totals, security identity and payload hashes are validated."
    )
    lines.append("")

    lines.extend([
        "## BSE fallback coverage",
        "",
        f"- **BSE corporate shareholding API access:** {bse_fallback.get('api_access_status', 'unknown')}.",
        f"- **Probe statuses:** {json.dumps(bse_fallback.get('status_counts', {}), sort_keys=True)}.",
        f"- **BSE UDiFF source SHA-256:** {bse_fallback.get('bse_udiff_source_sha256', 'N/A')}.",
        "An HTTP 403 is an access restriction, not proof that a filing does not exist. "
        "No third-party ownership percentages were substituted.",
        "",
        "## Scoring decision",
        "",
        "- **FII Accumulation Score:** N/A — stock-level verified net FPI transactions and a calibrated historical ownership-change series are not yet available for the full universe.",
        "- **Market Confirmation Score:** not published until its historical calibration and reproducibility gates pass.",
        "- **Composite score/ranking:** disabled.",
        "- Scoring weights, hard gates and calibration requirements are documented in docs/fii-accumulation-score-spec.md.",
        "- Market-wide FPI/DII flows are context only and are never treated as evidence that a specific stock was bought.",
        "",
        "## Reproducibility",
        "",
        f"- Matrix CSV SHA-256: {manifest.get('output_csv_sha256', 'N/A')}",
        f"- BSE fallback manifest SHA-256: {_sha256(bse_payload)}",
        f"- Ownership identity crosswalk SHA-256: {crosswalk or 'N/A'}",
        f"- NSDL normalized CSV SHA-256: {manifest.get('source_hashes', {}).get('nsdl_cash_flow_normalized_csv', 'N/A')}",
        f"- NSDL raw archive manifest SHA-256: {manifest.get('source_hashes', {}).get('nsdl_cash_flow_raw_archive_manifest', 'N/A')}",
        "- The accompanying run artifact retains raw source responses, manifests and normalized inputs."
        "",
        "This is an evidence watchlist, not an investment recommendation.",
    ])
    report = "\n".join(lines) + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    return {
        "output_path": str(output_path),
        "stock_count": len(rows),
        "tradable_stock_count": len(tradable),
        "placeholder_count": len(placeholders),
        "ownership_missing_count": len(missing_ownership),
        "candidate_count": len(candidates),
        "grade_1_count": len(grade_1),
        "grade_2_count": len(grade_2),
        "matrix_sha256": _sha256(matrix_payload),
        "bse_fallback_status": bse_fallback.get("api_access_status"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--bse-fallback-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(
        build_report(args.matrix, args.manifest, args.bse_fallback_manifest, args.output),
        indent=2,
    ))


if __name__ == "__main__":
    main()
