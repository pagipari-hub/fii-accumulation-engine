from pathlib import Path

import pytest

from fii_engine.yahoo_prices import (
    _separate_incomplete_latest_bars,
    read_symbols,
    yahoo_ticker,
)


def test_yahoo_ticker_maps_nse_symbols():
    assert yahoo_ticker("hdfcbank") == "HDFCBANK.NS"
    assert yahoo_ticker("NIFTYBEES.NS") == "NIFTYBEES.NS"
    assert yahoo_ticker("ABC.BO") == "ABC.BO"


def test_yahoo_ticker_rejects_blank_and_whitespace():
    with pytest.raises(ValueError):
        yahoo_ticker(" ")
    with pytest.raises(ValueError):
        yahoo_ticker("HDFC BANK")


def test_read_symbols_uses_symbol_column_and_deduplicates(tmp_path: Path):
    source = tmp_path / "symbols.csv"
    source.write_text(
        "company,symbol\nHDFC Bank,HDFCBANK\nICICI Bank,ICICIBANK\nhdfc,HDFCBANK\n"
    )
    assert read_symbols(source) == ["HDFCBANK", "ICICIBANK"]


def test_read_symbols_rejects_missing_symbol_header(tmp_path: Path):
    source = tmp_path / "symbols.csv"
    source.write_text("HDFCBANK\nICICIBANK\n")
    with pytest.raises(ValueError, match="symbol"):
        read_symbols(source)


def _bar(date: str, *, close: float | None = 100.0) -> dict:
    return {
        "date": date, "symbol": "HDFCBANK", "open": 99.0, "high": 101.0,
        "low": 98.0, "close": close, "adj_close": close, "volume": 1000,
        "dividends": 0.0, "stock_splits": 0.0,
    }


def test_incomplete_latest_bar_is_quarantined_not_used():
    rows = [_bar("2026-11-05"), _bar("2026-11-06", close=None)]
    complete, quarantined = _separate_incomplete_latest_bars(rows)
    assert [row["date"] for row in complete] == ["2026-11-05"]
    assert quarantined == [{
        "symbol": "HDFCBANK",
        "date": "2026-11-06",
        "missing_fields": ["close"],
        "reason": "incomplete_latest_bar",
    }]


def test_incomplete_historical_bar_fails_loudly():
    rows = [_bar("2026-11-05", close=None), _bar("2026-11-06")]
    with pytest.raises(RuntimeError, match="Incomplete historical bar"):
        _separate_incomplete_latest_bars(rows)


def test_symbol_with_no_complete_bar_fails_loudly():
    rows = [_bar("2026-11-06", close=None)]
    with pytest.raises(RuntimeError, match="No complete price bars"):
        _separate_incomplete_latest_bars(rows)
