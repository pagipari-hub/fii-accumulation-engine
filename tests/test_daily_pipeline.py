from pathlib import Path

import pytest

from fii_engine.daily_pipeline import _compact_result, _require_aligned_history


def test_compact_collector_result_uses_repository_relative_paths(tmp_path):
    result = {
        "raw_path": str(tmp_path / "data/raw/source.csv"),
        "reconciliation": {
            "manifest_path": str(tmp_path / "data/snapshots/source.json"),
            "mismatch_count": 0,
        },
        "source_url": "https://www.nseindia.com/example",
    }
    compact = _compact_result(result, tmp_path)
    assert compact["raw_path"] == "data/raw/source.csv"
    assert compact["reconciliation"]["manifest_path"] == "data/snapshots/source.json"
    assert compact["source_url"] == "https://www.nseindia.com/example"


def test_rejects_daily_and_historical_as_of_date_mismatch():
    matrix = {"trade_date": "2026-11-20"}
    history = {"as_of_trade_date": "2026-11-19"}

    with pytest.raises(ValueError, match="daily/historical as-of mismatch"):
        _require_aligned_history(matrix, history)


def test_accepts_matching_daily_and_historical_as_of_date():
    _require_aligned_history(
        {"trade_date": "2026-11-20"},
        {"as_of_trade_date": "2026-11-20"},
    )
