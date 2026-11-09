from __future__ import annotations

from fii_engine.matrix_scoring import _percentile_ranks, score_matrix_rows
from fii_engine.scoring import load_config


def _row(symbol, ret5, ret20, ret60, delivery5, delivery20, volume5, volume20):
    return {
        "symbol": symbol,
        "isin": f"INE000{symbol[:3]}01010",
        "as_of_trade_date": "2026-11-06",
        "historical_join_status": "isin_exact",
        "5s_cash_return_pct": str(ret5),
        "20s_cash_return_pct": str(ret20),
        "60s_cash_return_pct": str(ret60),
        "5s_cash_return_status": "complete",
        "20s_cash_return_status": "complete",
        "60s_cash_return_status": "complete",
        "5s_average_delivery_percentage": str(delivery5),
        "20s_average_delivery_percentage": str(delivery20),
        "5s_delivery_status": "complete",
        "20s_delivery_status": "complete",
        "5s_average_daily_volume": str(volume5),
        "20s_average_daily_volume": str(volume20),
        "5s_volume_status": "complete",
        "20s_volume_status": "complete",
        "deal_temporally_verified_fpi_buy_quantity": "0",
        "deal_temporally_verified_fpi_sell_quantity": "0",
        "deal_registry_temporal_status": "post_trade_snapshot_registration_start_unknown",
        "fpi_deal_evidence_grade": "grade_2_named_client_current_registry_match_temporal_status_unverified",
    }


def test_percentile_ranks_handle_ties_and_missing_values():
    assert _percentile_ranks([1.0, 2.0, 2.0, None, 4.0]) == [0.0, 0.375, 0.375, None, 1.0]
    assert _percentile_ranks([5.0]) == [0.5]
    assert _percentile_ranks([None, None]) == [None, None]


def test_current_matrix_adapter_keeps_fii_na_and_scores_confirmation():
    rows = [
        _row("AAA", -5, -10, -20, 30, 35, 80, 100),
        _row("BBB", 1, 2, 3, 40, 38, 110, 100),
        _row("CCC", 3, 4, 5, 50, 40, 130, 100),
        _row("DDD", 5, 8, 10, 60, 42, 160, 100),
    ]
    config = load_config(__import__("pathlib").Path(__file__).parents[1] / "config" / "scoring.json")
    result = score_matrix_rows(rows, config, matrix_ref="matrix.csv#sha256=abc")

    assert result["universe_row_count"] == 4
    assert result["fii_scoreable_row_count"] == 0
    assert result["fii_rankable_row_count"] == 0
    assert result["confirmation_scoreable_row_count"] == 4
    assert result["confirmation_rankable_row_count"] == 4

    top = result["rows"][-1]
    assert top["symbol"] == "DDD"
    assert top["fii_accumulation"]["score"] is None
    assert top["fii_accumulation"]["rankable"] is False
    assert top["market_confirmation"]["score"] is not None
    assert top["market_confirmation"]["rankable"] is True
    assert top["catalyst"]["status"] == "unavailable"
    assert top["catalyst"]["score"] is None


def test_unverified_deal_lead_never_becomes_verified_fii_score():
    rows = [_row("AAA", 1, 2, 3, 50, 45, 120, 100)]
    rows[0]["deal_registry_temporal_status"] = "post_trade_snapshot_registration_start_unknown"
    config = load_config(__import__("pathlib").Path(__file__).parents[1] / "config" / "scoring.json")
    result = score_matrix_rows(rows, config, matrix_ref="matrix.csv#sha256=abc")
    row = result["rows"][0]
    assert row["fii_accumulation"]["score"] is None
    assert row["fii_accumulation"]["status"] == "not_rankable_direct_fpi_gate_failed"
    assert row["deal_temporal_status"] == "post_trade_snapshot_registration_start_unknown"


def test_adapter_requires_unique_symbols():
    rows = [_row("AAA", 1, 2, 3, 50, 45, 120, 100)]
    rows.append(dict(rows[0]))
    config = load_config(__import__("pathlib").Path(__file__).parents[1] / "config" / "scoring.json")
    # Duplicate symbols are rejected by the CSV reader; direct adapter inputs
    # must still not silently collapse or overwrite rows.
    import pytest
    with pytest.raises(ValueError, match="unique"):
        score_matrix_rows(rows, config, matrix_ref="matrix.csv#sha256=abc")
