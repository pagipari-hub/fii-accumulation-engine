import pytest

from fii_engine.nifty500_universe import (
    discover_csv_link,
    normalized_csv,
    parse_constituent_csv,
    parse_page_updated_on,
)


def page(link='<a href="https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv">Download List of Nifty 500 Stocks (.csv)</a>'):
    return f"<html>{link}<p>Updated on: 28/10/2026</p></html>"


def fixture_csv(count=500):
    rows = ["Constituents list of Nifty 500", "",
            "Company Name,Industry,Symbol,Series,ISIN Code"]
    rows += [f"Company {i},Industry {i},SYM{i:03d},EQ,INE{i:09d}" for i in range(count)]
    return ("\n".join(rows) + "\n").encode()


def test_discovers_official_archive_link():
    assert discover_csv_link(page()).endswith("/content/indices/ind_nifty500list.csv")


def test_rejects_unexpected_host():
    html = page('<a href="https://example.com/list.csv">Download List of Nifty 500 Stocks (.csv)</a>')
    with pytest.raises(ValueError, match="unexpected"):
        discover_csv_link(html)


def test_requires_exactly_one_csv_link():
    with pytest.raises(ValueError, match="exactly one"):
        discover_csv_link("<html></html>")


def test_page_update_date_is_extracted_separately():
    assert parse_page_updated_on(page()) == "2026-10-28"


def test_parses_exactly_500_unique_members():
    records = parse_constituent_csv(fixture_csv())
    assert len(records) == 500
    assert records[0]["symbol"] == "SYM000"
    assert b"symbol,company_name,industry,series,isin" in normalized_csv(records)


def test_rejects_incomplete_universe():
    with pytest.raises(ValueError, match="expected 500"):
        parse_constituent_csv(fixture_csv(499))


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
