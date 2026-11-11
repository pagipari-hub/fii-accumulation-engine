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
    grade_1 = [row for row in candidates if row.get("fpi_deal_evidence_grade") == "grade_1"]
    grade_2 = [row for row in candidates if row.get("fpi_deal_evidence_grade") == "grade_2"]
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
            "These are aggregate market-wide flows only. They do not establish which individual stocks were bought or sold.",
            "",
        ])
    else:
        lines.extend([
            "NSDL aggregate cash-flow history has not been integrated into this matrix build.",
            "",
        ])

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
