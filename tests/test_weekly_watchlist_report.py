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
            "deal_current_registry_matched_fpi_client_buy_quantity": "12000000",
            "deal_current_registry_matched_fpi_client_sell_quantity": "0",
            "deal_unmatched_sell_quantity": "19098281",
            "deal_counterparty_identity_coverage_pct": "50.0",
            "5s_cash_return_pct": "-0.5083",
            "5s_average_delivery_percentage": "50.6360",
            "20s_cash_return_pct": "8.3482",
            "60s_cash_return_pct": "-7.4229",
            "shareholding_fpi_total_ownership_percentage_points": "4.5900",
            "shareholding_reporting_date": "2026-09-30",
            "delivery_context": "5s_delivery_at_or_above_20s",
            "daily_futures_data_status": "not_eligible",
            "daily_options_data_status": "not_applicable",
            "options_context": "5s_options_pcr_partial_observations",
            "ownership_context": "2026_quarterly_fpi_ownership_baseline_available;no_prior_quarter_change_series",
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
        "nse_shareholding_history_probe": {
            "row_counts": {"ALKEM": 2},
            "symbols_with_multiple_records": ["ALKEM"],
        },
        "nse_shareholding_unitholding_announcements_probe": {
            "matching_announcement_counts": {"EMBASSY": 2},
            "observations": [{
                "symbol": "EMBASSY",
                "attachments": [{"status": "captured"}, {"status": "download_error"}],
            }],
        },
        "nsdl_market_flow_context": {
            "start_date": "2026-08-10",
            "end_date": "2026-11-09",
            "normalized_date_count": 60,
            "fpi": {"cumulative_net_inr_crore": -255.21, "observed_days": 60, "positive_net_days": 30, "negative_net_days": 30, "gross_purchases_inr_crore": 100, "gross_sales_inr_crore": 110},
            "dii": {"cumulative_net_inr_crore": 231298.85, "observed_days": 60, "positive_net_days": 60, "negative_net_days": 0, "gross_purchases_inr_crore": 200, "gross_sales_inr_crore": 80},
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
    assert "Grade 2" in report
    assert "unverified" in report
    assert "incomplete buy/sell totals" in report
    assert "146 / 146 verified" in report
    assert "http_403_blocked" in report
    assert "Aggregate market-wide cash-flow context" in report
    assert "₹-255.21 crore" in report
    assert "₹100.00 / ₹110.00 crore" in report
    assert "These are aggregate market-wide flows only" in report
    assert "No sourced business/corporate catalyst" in report
    assert "Historical ownership source discovery" in report
    assert "ALKEM" in report and "EMBASSY" in report
    assert "Official attachments captured / failed" in report and "1 / 1" in report
    assert "12,000,000 / 0 shares" in report
    assert "19,098,281 shares" in report
    assert "50.00%" in report
    assert "-0.51%" in report and "+8.35%" in report and "-7.42%" in report
    assert "4.59%" in report and "2026-09-30" in report
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
