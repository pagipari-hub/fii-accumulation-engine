import csv
import hashlib
import json

from fii_engine.weekly_watchlist_report import build_report


def test_weekly_watchlist_is_conservative_and_hash_validated(tmp_path):
    matrix = tmp_path / "matrix.csv"
    rows = [
        {
            "symbol": "ABC",
            "company_name": "ABC Limited",
            "universe_member_type": "tradable_security",
            "fpi_scoring_eligible": "true",
            "direct_fpi_client_gross_buy_candidate": "true",
            "fpi_deal_evidence_grade": "grade_2",
            "deal_evidence_pattern": "matched_fpi_client_known_net_buy_partial",
            "deal_registry_temporal_status": "unverified",
            "deal_known_fpi_net_status": "partial",
            "shareholding_join_status": "parsed_verified_identity",
            "evidence_summary": "direct_named_fpi_client_gross_buy_candidate",
        },
        {
            "symbol": "DUMMYHEG",
            "company_name": "HEG Graphite Limited",
            "universe_member_type": "index_adjustment_placeholder",
            "fpi_scoring_eligible": "false",
            "direct_fpi_client_gross_buy_candidate": "false",
            "fpi_deal_evidence_grade": "not_applicable_index_placeholder",
            "deal_evidence_pattern": "not_applicable_index_placeholder",
            "deal_registry_temporal_status": "",
            "deal_known_fpi_net_status": "",
            "shareholding_join_status": "not_applicable_index_placeholder",
            "evidence_summary": "non_tradable_demerger_index_placeholder",
        },
    ]
    with matrix.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    digest = hashlib.sha256(matrix.read_bytes()).hexdigest()
    manifest = {
        "output_csv_sha256": digest,
        "stock_count": 2,
        "tradable_stock_count": 1,
        "index_placeholder_count": 1,
        "ownership_parsed_verified_count": 1,
        "ownership_coverage_pct": 100.0,
        "ownership_index_row_coverage_pct": 50.0,
        "ownership_status_counts": {
            "parsed_verified_identity": 1,
            "not_applicable_index_placeholder": 1,
        },
        "identity_crosswalk_candidate_count": 146,
        "identity_crosswalk_verified_count": 146,
        "identity_crosswalk_unresolved_count": 0,
        "identity_crosswalk_bse_source_sha256": "a" * 64,
        "source_hashes": {"shareholding_identity_crosswalk": "b" * 64},
        "direct_fpi_client_gross_buy_candidate_count": 1,
        "composite_score_status": "not_scored_independent_evidence_dimensions_only",
        "as_of_trade_date": "2026-11-09",
        "nsdl_market_flow_context": {
            "start_date": "2026-08-10",
            "end_date": "2026-11-09",
            "normalized_date_count": 60,
            "fpi": {"cumulative_net_inr_crore": -255.21, "observed_days": 60, "positive_net_days": 30, "negative_net_days": 30},
            "dii": {"cumulative_net_inr_crore": 231298.85, "observed_days": 60, "positive_net_days": 60, "negative_net_days": 0},
            "overlapping_fpi_dii_days": 60,
            "dii_no_data_days": ["2026-08-26"],
            "stock_level_attribution": False,
        },
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    fallback_path = tmp_path / "bse_fallback.json"
    fallback_path.write_text(json.dumps({
        "api_access_status": "http_403_blocked",
        "status_counts": {"probe_error": 1, "not_attempted_after_exchange_403": 5},
        "bse_udiff_source_sha256": "c" * 64,
    }), encoding="utf-8")

    output = tmp_path / "weekly.md"
    result = build_report(matrix, manifest_path, fallback_path, output)
    report = output.read_text(encoding="utf-8")

    assert result["candidate_count"] == 1
    assert result["tradable_stock_count"] == 1
    assert "ABC Limited" in report
    assert "grade_2" in report
    assert "unverified" in report
    assert "incomplete buy/sell totals" in report
    assert "146 / 146 verified" in report
    assert "http_403_blocked" in report
    assert "Aggregate market-wide cash-flow context" in report
    assert "₹-255.21 crore" in report
    assert "These are aggregate market-wide flows only" in report
    assert "Composite score/ranking" in report and "disabled." in report
    assert "not an investment recommendation" in report


def test_weekly_watchlist_rejects_matrix_hash_mismatch(tmp_path):
    matrix = tmp_path / "matrix.csv"
    matrix.write_text("symbol\nABC\n", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({
        "output_csv_sha256": "0" * 64,
        "stock_count": 1,
        "composite_score_status": "not_scored_independent_evidence_dimensions_only",
    }), encoding="utf-8")
    fallback_path = tmp_path / "bse_fallback.json"
    fallback_path.write_text("{}", encoding="utf-8")

    try:
        build_report(matrix, manifest_path, fallback_path, tmp_path / "weekly.md")
    except ValueError as exc:
        assert "SHA-256" in str(exc)
    else:
        raise AssertionError("matrix hash mismatch was accepted")
