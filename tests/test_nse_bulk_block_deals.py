import json
from datetime import date

import pytest

from fii_engine.nse_bulk_block_deals import (
    _crosscheck_json_csv,
    _parse_csv,
    _parse_json,
    _parse_query_date,
)


CSV_HEADER = (
    "Date ,Symbol ,Security Name ,Client Name ,Buy / Sell ,"
    "Quantity Traded ,Trade Price / Wght. Avg. Price ,Remarks \n"
)
CSV_ROWS = (
    "30-OCT-2026,ABC,ABC Limited,ALPHA FUND,BUY,2,00,000,40.51,-\n"
    "30-OCT-2026,XYZ,XYZ Limited,BETA LLP,SELL,1,25,000,100.00,\n"
)


def _csv_payload(rows=CSV_ROWS):
    # Quantity values use Indian comma grouping; quote CSV fields containing commas.
    return (
        "Date ,Symbol ,Security Name ,Client Name ,Buy / Sell ,"
        "Quantity Traded ,Trade Price / Wght. Avg. Price ,Remarks \n"
        '"30-OCT-2026",ABC,ABC Limited,ALPHA FUND,BUY,"2,00,000",40.51,-\n'
        '"30-OCT-2026",XYZ,XYZ Limited,BETA LLP,SELL,"1,25,000",100.00,\n'
        if rows is CSV_ROWS else rows
    ).encode()


def _json_payload(rows=None):
    if rows is None:
        rows = [{
            "BD_DT_DATE": "30-OCT-2026",
            "BD_DT_ORDER": "2026-10-29T18:30:00.000Z",
            "BD_SYMBOL": "ABC",
            "BD_SCRIP_NAME": "ABC Limited",
            "BD_CLIENT_NAME": "ALPHA FUND",
            "BD_BUY_SELL": "BUY",
            "BD_QTY_TRD": 200000,
            "BD_TP_WATP": 40.51,
            "BD_REMARKS": "-",
        }]
    return json.dumps({"data": rows}).encode()


def test_parses_official_deals_csv_with_indian_quantity_grouping():
    result = _parse_csv(
        _csv_payload(), "bulk", date(2026, 10, 30), date(2026, 11, 6)
    )
    assert result["row_count"] == 2
    assert result["distinct_symbols"] == 2
    assert result["side_counts"] == {"BUY": 1, "SELL": 1}
    assert result["rows"][0]["quantity"] == "200000"
    assert result["rows"][0]["weighted_average_price"] == "40.51"
    assert result["rows"][0]["trade_date"] == "2026-10-30"


def test_parses_json_using_explicit_deal_date_not_utc_order_timestamp():
    result = _parse_json(
        _json_payload(), "bulk", date(2026, 10, 30), date(2026, 11, 6)
    )
    assert result["row_count"] == 1
    assert result["rows"][0]["trade_date"] == "2026-10-30"
    assert result["rows"][0]["quantity"] == "200000"


def test_json_subset_crosscheck_records_incomplete_ui_payload():
    csv_rows = _parse_csv(
        _csv_payload(), "bulk", date(2026, 10, 30), date(2026, 11, 6)
    )["rows"]
    json_rows = _parse_json(
        _json_payload(), "bulk", date(2026, 10, 30), date(2026, 11, 6)
    )["rows"]
    result = _crosscheck_json_csv(json_rows, csv_rows)
    assert result["json_is_subset_of_csv"] is True
    assert result["json_complete_for_range"] is False
    assert result["json_row_count"] == 1
    assert result["csv_row_count"] == 2
    assert result["json_coverage_ratio"] == 0.5


def test_rejects_json_record_absent_from_csv_export():
    csv_rows = _parse_csv(
        _csv_payload(), "bulk", date(2026, 10, 30), date(2026, 11, 6)
    )["rows"]
    bad_json = _parse_json(
        _json_payload([{
            "BD_DT_DATE": "30-OCT-2026", "BD_DT_ORDER": "ignored",
            "BD_SYMBOL": "MISSING", "BD_SCRIP_NAME": "Missing Ltd",
            "BD_CLIENT_NAME": "CLIENT", "BD_BUY_SELL": "BUY",
            "BD_QTY_TRD": 1, "BD_TP_WATP": 10, "BD_REMARKS": None,
        }]),
        "bulk", date(2026, 10, 30), date(2026, 11, 6),
    )["rows"]
    with pytest.raises(ValueError, match="absent from CSV"):
        _crosscheck_json_csv(bad_json, csv_rows)


def test_accepts_valid_header_with_no_deals_as_zero_rows():
    payload = (
        "Date ,Symbol ,Security Name ,Client Name ,Buy / Sell ,"
        "Quantity Traded ,Trade Price / Wght. Avg. Price ,Remarks \n"
    ).encode()
    result = _parse_csv(
        payload, "block", date(2026, 10, 30), date(2026, 11, 6)
    )
    assert result["row_count"] == 0
    assert result["rows"] == []


def test_rejects_deal_date_outside_requested_range():
    payload = (
        "Date ,Symbol ,Security Name ,Client Name ,Buy / Sell ,"
        "Quantity Traded ,Trade Price / Wght. Avg. Price ,Remarks \n"
        '"29-OCT-2026",ABC,ABC Limited,ALPHA FUND,BUY,"2,00,000",40.51,-\n'
    ).encode()
    with pytest.raises(ValueError, match="outside requested range"):
        _parse_csv(payload, "bulk", date(2026, 10, 30), date(2026, 11, 6))


def test_rejects_invalid_side_and_nonpositive_quantity():
    base = (
        "Date ,Symbol ,Security Name ,Client Name ,Buy / Sell ,"
        "Quantity Traded ,Trade Price / Wght. Avg. Price ,Remarks \n"
        '"30-OCT-2026",ABC,ABC Limited,ALPHA FUND,HOLD,100,40.51,-\n'
    ).encode()
    with pytest.raises(ValueError, match="invalid NSE deal side"):
        _parse_csv(base, "bulk", date(2026, 10, 30), date(2026, 11, 6))


def test_rejects_missing_required_csv_columns():
    payload = b"Date,Symbol,Quantity Traded\n30-OCT-2026,ABC,100\n"
    with pytest.raises(ValueError, match="schema drift"):
        _parse_csv(payload, "bulk", date(2026, 10, 30), date(2026, 11, 6))


def test_parses_date_query_format_exactly():
    assert _parse_query_date("30-10-2026") == date(2026, 10, 30)
    with pytest.raises(ValueError, match="DD-MM-YYYY"):
        _parse_query_date("2026-10-30")
