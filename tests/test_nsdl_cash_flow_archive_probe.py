from datetime import date

import pytest

from fii_engine.nsdl_cash_flow_archive_probe import (
    classify_response,
    hidden_fields,
    month_query_dates,
    weekdays,
)


def test_hidden_fields_preserve_webforms_state():
    html = """<form><input type="hidden" name="__VIEWSTATE" value="abc&amp;123">
    <input type="hidden" name="__EVENTVALIDATION" value="token"></form>"""
    assert hidden_fields(html) == {
        "__VIEWSTATE": "abc&123",
        "__EVENTVALIDATION": "token",
    }


def test_month_query_dates_return_one_clipped_month_end_per_month():
    assert month_query_dates(date(2026, 8, 17), date(2026, 11, 10)) == [
        date(2026, 8, 31),
        date(2026, 9, 30),
        date(2026, 10, 31),
        date(2026, 11, 10),
    ]


def test_weekdays_exclude_saturday_and_sunday():
    assert weekdays(date(2026, 11, 6), date(2026, 11, 10)) == [
        date(2026, 11, 6),
        date(2026, 11, 9),
        date(2026, 11, 10),
    ]


def test_fpi_response_requires_expected_table_headers():
    result = classify_response(
        "fpi",
        b"<table><tr><th>Reporting Date</th><th>Gross Purchases</th></tr></table>",
    )
    assert result["response_type"] == "data"


def test_dii_no_data_is_explicit_not_zero():
    assert classify_response("dii", b"<p>No Data Found</p>") == {
        "response_type": "no_data"
    }


def test_dii_requires_expected_table():
    result = classify_response(
        "dii",
        b"<table><tr><th>Type of DII</th><th>Gross Purchases</th></tr></table>",
    )
    assert result == {"response_type": "data", "has_dii_table": True}


@pytest.mark.parametrize(
    "payload",
    [
        b"<html>Access Denied</html>",
        b"<html>Request blocked by security</html>",
    ],
)
def test_block_pages_are_rejected(payload):
    with pytest.raises(ValueError):
        classify_response("dii", payload)
