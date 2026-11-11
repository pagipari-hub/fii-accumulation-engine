from fii_engine.nse_security_master_probe import inspect_csv


def test_inspects_official_csv_schema_and_sample_rows():
    result = inspect_csv(
        b"SYMBOL,NAME,ISIN\nABC,ABC Limited,INE000A01001\n",
        source_name="equity_master",
    )
    assert result["header"] == ["SYMBOL", "NAME", "ISIN"]
    assert result["row_count"] == 1
    assert result["sample_rows"][0]["ISIN"] == "INE000A01001"


def test_rejects_html_disguised_as_csv():
    import pytest

    with pytest.raises(ValueError, match="HTML"):
        inspect_csv(b"<!doctype html><html>blocked</html>", source_name="equity_master")


def test_rejects_duplicate_headers():
    import pytest

    with pytest.raises(ValueError, match="duplicate"):
        inspect_csv(b"SYMBOL,SYMBOL\nABC,ABC\n", source_name="symbol_changes")


def test_rejects_empty_csv():
    import pytest

    with pytest.raises(ValueError, match="empty"):
        inspect_csv(b"", source_name="company_name_changes")


def test_inspects_compressed_historical_mii_security_master():
    import gzip

    from fii_engine.nse_security_master_probe import inspect_gzip_csv

    payload = gzip.compress(b"SYMBOL,SERIES,ISIN\\nABC,EQ,INE000A01001\\n")
    result = inspect_gzip_csv(payload, source_name="historical_mii_security_master")
    assert result["header"] == ["SYMBOL", "SERIES", "ISIN"]
    assert result["row_count"] == 1
    assert result["sample_rows"][0]["ISIN"] == "INE000A01001"


def test_rejects_invalid_gzip_historical_master():
    import pytest

    from fii_engine.nse_security_master_probe import inspect_gzip_csv

    with pytest.raises(ValueError, match="gzip"):
        inspect_gzip_csv(b"not-gzip", source_name="historical_mii_security_master")
