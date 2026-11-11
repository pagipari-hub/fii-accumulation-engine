import csv
import hashlib
import json
from pathlib import Path

from fii_engine.fpi_evidence_blend import build


def _write_csv(path: Path, rows: list[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_build_blended_matrix_preserves_missingness_and_full_universe(tmp_path):
    universe = [{
        "symbol": "ABC", "company_name": "ABC Limited", "industry": "Industrials",
        "series": "EQ", "isin": "INE000A01001",
    }]
    for index in range(500):
        universe.append({
            "symbol": "TEST" + str(index).zfill(3),
            "company_name": "Test Company " + str(index),
            "industry": "Test",
            "series": "EQ",
            "isin": "INE" + str(index + 1).zfill(9),
        })
    universe_hash = _write_csv(
        tmp_path / "data/raw/universe/nifty500_current.csv", universe
    )
    _write_json(tmp_path / "data/snapshots/universe/nifty500_test.json", {
        "normalized_sha256": universe_hash,
        "retrieved_at_utc": "2026-11-07T06:34:22+00:00",
        "source_freshness_statement": "Data as on last trading day of the previous month",
        "source_freshness_semantics": "exact constituent effective date not supplied here",
        "publisher": "NSE Indices",
        "source_page": "https://www.niftyindices.com/indices/equity/broad-based-indices/NIFTY-500",
    })
    _write_csv(tmp_path / "data/audits/nifty500_index_placeholders_2026-11-09.csv", [{
        "symbol": "TEST000",
        "security_name": "Test demerged entity",
        "classification": "demerged_index_placeholder",
        "effective_date": "2026-09-07",
        "price_treatment": "zero_price_without_divisor_adjustment",
        "source_title": "Test index placeholder notice",
        "source_date": "2026-09-03",
        "source_url": "https://www.niftyindices.com/press-release",
        "reason": "Non-tradable index placeholder",
    }])
    _write_json(tmp_path / "data/processed/nsdl_cash_flow_context.json", {
        "scope": "aggregate_market_wide_cash_equity_context_only",
        "stock_level_attribution": False,
        "normalized_date_count": 60,
        "fpi": {"cumulative_net_inr_crore": -10.0},
        "dii": {"cumulative_net_inr_crore": 20.0},
    })

    daily_rows = []
    historical_rows = []
    deal_rows = []
    share_rows = []
    for item in universe:
        symbol, isin = item["symbol"], item["isin"]
        daily_rows.append({
            "symbol": symbol, "isin": isin, "trade_date": "2026-11-06",
            "fno_status": "eligible", "cash_data_status": "observed",
            "cash_close": "100", "cash_previous_close": "99",
            "cash_return_pct": "1.01", "cash_volume": "10000",
            "cash_turnover": "1000000", "cash_trade_count": "500",
            "delivery_status": "reported", "delivery_quantity": "5000",
            "delivery_percentage": "50", "futures_data_status": "observed",
            "futures_expiry": "2026-11-26", "futures_close": "101",
            "futures_return_pct": "1.5", "futures_open_interest": "100000",
            "futures_oi_change": "5000", "futures_oi_change_pct": "5",
            "futures_volume": "25000", "futures_buildup": "long_buildup",
            "options_data_status": "observed", "options_expiry": "2026-11-26",
            "options_pcr_oi": "1.1", "options_pcr_volume": "1.0",
        })
        historical = {"trade_date": "2026-11-06", "symbol": symbol, "isin": isin}
        for window in (5, 20, 60):
            historical.update({
                f"{window}s_status": "complete",
                f"{window}s_sessions_available": str(window),
                f"{window}s_stock_observations": str(window),
                f"{window}s_cash_return_pct": "2" if window == 5 else "4" if window == 20 else "-1",
                f"{window}s_cash_return_status": "complete",
                f"{window}s_average_daily_volume": "10000",
                f"{window}s_volume_status": "complete",
                f"{window}s_average_delivery_percentage": "55" if window == 5 else "45" if window == 20 else "50",
                f"{window}s_delivery_status": "complete",
                f"{window}s_futures_observations": "5",
                f"{window}s_futures_buildup_counts": '{"long_buildup":3,"short_buildup":1}',
                f"{window}s_futures_buildup_status": "complete",
                f"{window}s_average_options_pcr_oi_same_expiry": "1.1" if window == 5 else "",
                f"{window}s_options_pcr_status": "complete_same_expiry" if window == 5 else "contract_roll_or_partial_data",
            })
        historical_rows.append(historical)

        deal = {
            "symbol": symbol, "universe_isin": isin,
            "deal_data_status": "no_disclosed_deal_in_available_snapshots",
            "deal_event_count": "0", "unverified_deal_event_count": "0",
            "total_deal_event_count": "0", "source_record_count": "0",
            "unverified_source_record_count": "0", "cross_report_duplicate_event_count": "0",
            "current_registry_matched_fpi_client_event_count": "0",
            "current_registry_matched_odi_issuer_event_count": "0",
            "ambiguous_registration_event_count": "0", "unmatched_event_count": "0",
            "current_registry_matched_fpi_client_buy_quantity": "0",
            "current_registry_matched_fpi_client_sell_quantity": "0",
            "current_registry_matched_fpi_client_known_net_quantity": "0",
            "current_registry_matched_fpi_client_buy_value": "0",
            "current_registry_matched_fpi_client_sell_value": "0",
            "current_registry_matched_odi_issuer_buy_quantity": "0",
            "current_registry_matched_odi_issuer_sell_quantity": "0",
            "unmatched_buy_quantity": "0", "unmatched_sell_quantity": "0",
            "temporally_verified_fpi_buy_quantity": "0",
            "temporally_verified_fpi_sell_quantity": "0",
            "temporally_unverified_fpi_buy_quantity": "0",
            "temporally_unverified_fpi_sell_quantity": "0",
            "counterparty_identity_coverage_pct": "",
            "known_fpi_net_status": "no_disclosed_deal_in_available_snapshots",
            "registry_temporal_status": "no_current_registry_matched_fpi_clients",
        }
        if symbol == "ABC":
            deal.update({
                "deal_data_status": "observed_disclosed_deals",
                "deal_event_count": "1", "total_deal_event_count": "1",
                "source_record_count": "1",
                "current_registry_matched_fpi_client_event_count": "1",
                "unmatched_event_count": "1",
                "current_registry_matched_fpi_client_buy_quantity": "100",
                "current_registry_matched_fpi_client_known_net_quantity": "100",
                "current_registry_matched_fpi_client_buy_value": "10000",
                "unmatched_sell_quantity": "50",
                "temporally_unverified_fpi_buy_quantity": "100",
                "counterparty_identity_coverage_pct": "50.0",
                "known_fpi_net_status": "partial_unmatched_or_odi_counterparties",
                "registry_temporal_status": "post_trade_snapshot_registration_start_unknown",
            })
        deal_rows.append(deal)

        share = {
            "reporting_date": "2026-09-30", "symbol": symbol,
            "company_name": item["company_name"],
            "isin": isin if symbol == "ABC" else "OLD" + isin,
            "record_id": "123" if symbol == "ABC" else "",
            "submission_date": "2026-10-06" if symbol == "ABC" else "",
            "broadcast_date": "", "system_date": "", "revised_data": "N",
            "revision_date": "", "identity_status": "verified" if symbol == "ABC" else "isin_mismatch",
            "identity_join_method": "exact_isin" if symbol == "ABC" else "isin_conflict",
            "filing_status": "parsed" if symbol == "ABC" else "isin_mismatch",
            "fpi_category_i_shares": "1000" if symbol == "ABC" else "",
            "fpi_category_ii_shares": "250" if symbol == "ABC" else "",
            "fpi_total_shares": "1250" if symbol == "ABC" else "",
            "fpi_category_i_ownership_percentage_points": "10.0" if symbol == "ABC" else "",
            "fpi_category_ii_ownership_percentage_points": "2.5" if symbol == "ABC" else "",
            "fpi_total_ownership_percentage_points": "12.5" if symbol == "ABC" else "",
            "other_foreign_institution_shares": "", "overseas_depositories_shares": "",
            "foreign_direct_investment_shares": "", "foreign_venture_capital_investor_shares": "",
            "sovereign_wealth_funds_foreign_shares": "", "foreign_institutions_total_shares": "",
            "fpi_reconciliation_status": "matched" if symbol == "ABC" else "not_available",
        }
        share_rows.append(share)

    daily_hash = _write_csv(tmp_path / "data/raw/daily_stock_features/daily_stock_features_current.csv", daily_rows)
    historical_hash = _write_csv(tmp_path / "data/raw/historical_features/stock_historical_features_current.csv", historical_rows)
    deal_hash = _write_csv(tmp_path / "data/raw/fpi_deal_attribution/fpi_deal_stock_summary_current.csv", deal_rows)
    share_hash = _write_csv(tmp_path / "data/raw/shareholding/shareholding_ownership_current.csv", share_rows)
    market_rows = [
        {"as_of_trade_date": "2026-11-06", "category": category,
         "window_sessions": str(window), "status": "insufficient_history",
         "sessions_available": "1"}
        for category in ("FII/FPI", "DII") for window in (5, 20, 60)
    ]
    participant_rows = [{
        "dataset": dataset, "participant_category": "FII", "instrument_group": "stock_futures",
        "option_type": "", "source_side": "long", "window_sessions": "5",
        "sessions_available": "1", "scope": "participant_category_aggregate_not_stock_specific",
        "status": "insufficient_history", "start_contracts": "", "end_contracts": "",
        "change_contracts": "", "change_pct": "", "total_traded_contracts": "",
        "average_daily_contracts": "",
    } for dataset in ("participant_oi", "participant_volume")]
    market_hash = _write_csv(tmp_path / "data/raw/historical_features/market_flow_history_current.csv", market_rows)
    participant_hash = _write_csv(tmp_path / "data/raw/historical_features/participant_derivatives_history_current.csv", participant_rows)

    _write_json(tmp_path / "data/raw/daily_stock_features/daily_stock_features_current_manifest.json", {
        "trade_date": "2026-11-06", "output_sha256": daily_hash, "output_row_count": 501,
    })
    _write_json(tmp_path / "data/raw/historical_features/historical_features_current_manifest.json", {
        "as_of_trade_date": "2026-11-06", "stock_output_sha256": historical_hash,
        "market_output_sha256": market_hash, "participant_output_sha256": participant_hash,
        "daily_stock_session_count": 60, "market_flow_session_count": 1,
        "participant_oi_session_count": 1, "participant_volume_session_count": 1,
    })
    _write_json(tmp_path / "data/raw/fpi_deal_attribution/fpi_deal_attribution_current_manifest.json", {
        "stock_summary_csv_sha256": deal_hash, "universe_csv_sha256": universe_hash,
        "trade_date_window_start": "2026-10-30", "trade_date_window_end": "2026-11-06",
        "registry_snapshot_date": "2026-11-08",
        "registry_snapshot_sha256": "a" * 64,
    })
    _write_json(tmp_path / "data/raw/shareholding/current_manifest.json", {
        "ownership_current_sha256": share_hash, "universe_sha256": universe_hash,
        "latest_reporting_date": "2026-09-30", "latest_quarter_coverage_pct": 0.2,
    })

    result = build(tmp_path)
    output_path = tmp_path / result["current_output_path"]
    with output_path.open("r", encoding="utf-8", newline="") as handle:
        output = list(csv.DictReader(handle))
    assert result["stock_count"] == 501
    assert result["tradable_stock_count"] == 500
    assert result["index_placeholder_count"] == 1
    assert result["nsdl_market_flow_context"]["stock_level_attribution"] is False
    assert result["nsdl_market_flow_context"]["scope"] == "aggregate_market_wide_cash_equity_context_only"
    assert result["ownership_coverage_pct"] == 0.2
    assert result["ownership_index_row_coverage_pct"] == 0.2
    assert result["ownership_status_counts"]["not_applicable_index_placeholder"] == 1
    assert result["composite_score_status"] == "not_scored_independent_evidence_dimensions_only"
    assert result["ownership_parsed_verified_count"] == 1
    assert len(output) == 501

    abc = next(row for row in output if row["symbol"] == "ABC")
    no_data = next(row for row in output if row["symbol"] == "TEST001")
    placeholder = next(row for row in output if row["symbol"] == "TEST000")
    assert abc["direct_fpi_client_gross_buy_candidate"] == "true"
    assert abc["fpi_deal_evidence_grade"].startswith("grade_2")
    assert abc["5s_cash_return_pct"] == "2"
    assert abc["short_term_price_context"] == "5s_positive_price_return"
    assert abc["positional_price_context"] == "20s_positive_60s_negative"
    assert abc["delivery_context"] == "5s_delivery_strengthening_vs_20s"
    assert abc["shareholding_fpi_total_ownership_percentage_points"] == "12.5"
    assert abc["composite_score_status"] == "not_scored_independent_evidence_dimensions_only"
    assert no_data["deal_deal_data_status"] == "no_disclosed_deal_in_available_snapshots"
    assert no_data["shareholding_join_status"] == "isin_mismatch"
    assert no_data["shareholding_fpi_total_ownership_percentage_points"] == ""
    assert no_data["direct_fpi_client_gross_buy_candidate"] == "false"
    assert placeholder["universe_member_type"] == "index_adjustment_placeholder"
    assert placeholder["tradability_status"] == "non_tradable_zero_price"
    assert placeholder["fpi_scoring_eligible"] == "false"
    assert placeholder["shareholding_join_status"] == "not_applicable_index_placeholder"
    assert placeholder["fpi_deal_evidence_grade"] == "not_applicable_index_placeholder"
    assert placeholder["evidence_summary"] == "non_tradable_demerger_index_placeholder"
    assert len(result["market_flow_window_statuses"]) == 6
    assert all(item["status"] == "insufficient_history" for item in result["market_flow_window_statuses"])

    second_result = build(tmp_path)
    assert second_result["snapshot_manifest_path"] == result["snapshot_manifest_path"]
    assert second_result["generated_at_utc"] == result["generated_at_utc"]
