import pytest

from fii_engine.nifty500_universe import (
    CSV_URL,
    parse_constituent_csv,
    parse_page_freshness,
    normalized_csv,
)


def page():
    return "<html><h1>NIFTY 500</h1><p>Data as on last trading day of the previous month</p></html>"


def fixture_csv(count=500):
    rows = ["Constituents list of Nifty 500", "",
            "Company Name,Industry,Symbol,Series,ISIN Code"]
    rows += [f"Company {i},Industry {i},SYM{i:03d},EQ,INE{i:09d}" for i in range(count)]
    return ("\n".join(rows) + "\n").encode()


def test_download_url_is_on_official_nse_indices_domain():
    assert CSV_URL.startswith("https://www.niftyindices.com/")


def test_requires_official_page_freshness_statement():
    assert parse_page_freshness(page()) == "Data as on last trading day of the previous month"


def test_rejects_changed_or_missing_freshness_statement():
    with pytest.raises(ValueError, match="freshness statement"):
        parse_page_freshness("<html>NIFTY 500</html>")


def test_parses_500_and_normalizes_columns():
    records = parse_constituent_csv(fixture_csv())
    assert len(records) == 500
    assert records[0]["symbol"] == "SYM000"
    assert b"symbol,company_name,industry,series,isin" in normalized_csv(records)


def test_accepts_official_index_constituent_count_above_500():
    assert len(parse_constituent_csv(fixture_csv(504))) == 504


def test_rejects_implausible_or_incomplete_constituent_count():
    with pytest.raises(ValueError, match="outside safety range"):
        parse_constituent_csv(fixture_csv(479))


def test_rejects_duplicate_symbol():
    payload = fixture_csv().decode().replace("SYM001", "SYM000")
    with pytest.raises(ValueError, match="duplicate NSE symbols"):
        parse_constituent_csv(payload.encode())


def test_rejects_blank_required_field():
    payload = fixture_csv().decode().replace("Company 1,Industry 1,SYM001,EQ,", "Company 1,Industry 1,SYM001,,")
    with pytest.raises(ValueError, match="blank required field"):
        parse_constituent_csv(payload.encode())


def test_rejects_schema_drift():
    with pytest.raises(ValueError, match="schema drift"):
        parse_constituent_csv(b"Company,Symbol\nExample,ABC\n")
