from pathlib import Path

import pytest

from fii_engine.yahoo_prices import read_symbols, yahoo_ticker


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


def test_read_symbols_supports_first_column_without_header(tmp_path: Path):
    source = tmp_path / "symbols.csv"
    source.write_text("HDFCBANK\nICICIBANK\n")
    assert read_symbols(source) == ["ICICIBANK"]
