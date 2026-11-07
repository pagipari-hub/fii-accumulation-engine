import csv
import io
import zipfile

import pytest

from fii_engine.nse_cash_market import (
    _select,
    _capture,
    validate_delivery,
    validate_udiff,
)


def _csv_bytes(headers, rows):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode()


def _udiff_zip(trade_date="2026-11-06"):
    headers = [
        "TradDt", "BizDt", "Sgmt", "Src", "FinInstrmTp", "FinInstrmId",
        "ISIN", "TckrSymb", "SctySrs", "OpnPric", "HghPric", "LwPric",
        "ClsPric", "LastPric", "PrvsClsgPric", "TtlTradgVol", "TtlTrfVal",
        "TtlNbOfTxsExctd",
    ]
    row = {
        "TradDt": trade_date, "BizDt": trade_date, "Sgmt": "CM", "Src": "NSE",
        "FinInstrmTp": "STK", "FinInstrmId": "12345", "ISIN": "INE000A01001",
        "TckrSymb": "ABC", "SctySrs": "EQ", "OpnPric": "100",
        "HghPric": "105", "LwPric": "99", "ClsPric": "103",
        "LastPric": "103", "PrvsClsgPric": "98", "TtlTradgVol": "100000",
        "TtlTrfVal": "10200000", "TtlNbOfTxsExctd": "1200",
    }
    csv_payload = _csv_bytes(headers, [row])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("BhavCopy_NSE_CM.csv", csv_payload)
    return buffer.getvalue()


def _delivery_csv(**overrides):
    headers = [
        "SYMBOL", "SERIES", "DATE1", "PREV_CLOSE", "OPEN_PRICE",
        "HIGH_PRICE", "LOW_PRICE", "LAST_PRICE", "CLOSE_PRICE", "AVG_PRICE",
        "TTL_TRD_QNTY", "TURNOVER_LACS", "NO_OF_TRADES", "DELIV_QTY", "DELIV_PER",
    ]
    row = {
        "SYMBOL": "ABC", "SERIES": "EQ", "DATE1": "06-Nov-2026",
        "PREV_CLOSE": "98", "OPEN_PRICE": "100", "HIGH_PRICE": "105",
        "LOW_PRICE": "99", "LAST_PRICE": "103", "CLOSE_PRICE": "103",
        "AVG_PRICE": "102", "TTL_TRD_QNTY": "100000",
        "TURNOVER_LACS": "1020", "NO_OF_TRADES": "1200",
        "DELIV_QTY": "30000", "DELIV_PER": "30.00",
    }
    row.update(overrides)
    return _csv_bytes(headers, [row])


def _catalogue():
    return {
        "currentDate": "06-Nov-2026",
        "previousDate": "05-Nov-2026",
        "CurrentDay": [
            {
                "displayName": "CM-UDiFF Common Bhavcopy Final (zip)",
                "fileActlName": "BhavCopy_NSE_CM_0_0_0_20261106_F_0000.csv.zip",
                "filePath": "https://nsearchives.nseindia.com/content/cm",
                "tradingDate": "06-Nov-2026",
                "fileSize": "1000",
            },
            {
                "displayName": "Full Bhavcopy and Security Deliverable data",
                "fileActlName": "sec_bhavdata_full_06112026.csv",
                "filePath": "https://nsearchives.nseindia.com/products/content",
                "tradingDate": "06-Nov-2026",
                "fileSize": "1000",
            },
        ],
        "PreviousDay": [],
    }


def test_selects_official_catalogue_records_without_guessing_archive_urls():
    catalogue = _catalogue()
    udiff = _select(catalogue, "udiff")
    delivery = _select(catalogue, "delivery")
    assert udiff["trading_date"] == delivery["trading_date"] == "2026-11-06"
    assert udiff["download_url"].endswith(
        "/BhavCopy_NSE_CM_0_0_0_20261106_F_0000.csv.zip"
    )
    assert delivery["download_url"].endswith("/sec_bhavdata_full_06112026.csv")


def test_rejects_untrusted_catalogue_archive_host():
    catalogue = _catalogue()
    catalogue["CurrentDay"][0]["filePath"] = "https://example.com/content/cm"
    with pytest.raises(ValueError, match="untrusted"):
        _select(catalogue, "udiff")


def test_validates_cm_udiff_schema_and_trade_date():
    result = validate_udiff(_udiff_zip(), "2026-11-06")
    assert result["row_count"] == 1
    assert result["distinct_symbols"] == 1
    assert result["instrument_type_counts"] == {"STK": 1}
    assert result["csv_bytes"].startswith(b"TradDt")


def test_rejects_cm_udiff_trade_date_mismatch():
    with pytest.raises(ValueError, match="TradDt mismatch"):
        validate_udiff(_udiff_zip("2026-11-05"), "2026-11-06")


def test_rejects_cm_udiff_empty_or_missing_required_columns():
    headers = ["TradDt", "BizDt"]
    csv_payload = _csv_bytes(headers, [{"TradDt": "2026-11-06", "BizDt": "2026-11-06"}])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("file.csv", csv_payload)
    with pytest.raises(ValueError, match="schema drift"):
        validate_udiff(buffer.getvalue(), "2026-11-06")


def test_validates_delivery_qty_and_percentage():
    result = validate_delivery(_delivery_csv(), "2026-11-06")
    assert result["row_count"] == 1
    assert result["distinct_symbols"] == 1
    assert result["series_counts"] == {"EQ": 1}


def test_rejects_delivery_percentage_mismatch():
    with pytest.raises(ValueError, match="percentage mismatch"):
        validate_delivery(_delivery_csv(DELIV_PER="50.00"), "2026-11-06")


def test_rejects_delivery_date_mismatch():
    with pytest.raises(ValueError, match="date mismatch"):
        validate_delivery(_delivery_csv(DATE1="05-Nov-2026"), "2026-11-06")


def test_rejects_missing_delivery_values_instead_of_zero_filling():
    with pytest.raises(ValueError, match="incomplete delivery quantity/percentage"):
        validate_delivery(_delivery_csv(DELIV_QTY=""), "2026-11-06")


def test_rejects_delivery_quantity_greater_than_traded_quantity():
    with pytest.raises(ValueError, match="invalid traded/delivered quantity"):
        validate_delivery(_delivery_csv(DELIV_QTY="100001"), "2026-11-06")


def test_capture_preserves_human_formatted_catalogue_file_size(monkeypatch):
    import fii_engine.nse_cash_market as module

    record = {
        "kind": "delivery",
        "download_url": "https://nsearchives.nseindia.com/products/content/file.csv",
        "reported_file_size": "203.19 KB",
    }
    monkeypatch.setattr(module, "_get", lambda *args, **kwargs: b"csv-data")
    assert _capture(record) == b"csv-data"
    assert record["reported_file_size"] == "203.19 KB"


def test_dash_delivery_fields_remain_unavailable_not_zero():
    result = validate_delivery(
        _delivery_csv(
            TTL_TRD_QNTY="0", DELIV_QTY="-", DELIV_PER="-",
            OPEN_PRICE="-", HIGH_PRICE="-", LOW_PRICE="-", CLOSE_PRICE="-",
        ),
        "2026-11-06",
    )
    assert result["delivery_observation_counts"]["no_trades_delivery_not_reported"] == 1
    assert result["delivery_observation_counts"]["reported"] == 0


def test_all_dash_delivery_observations_are_not_reported_not_zero():
    result = validate_delivery(
        _delivery_csv(
            TTL_TRD_QNTY="-", DELIV_QTY="-", DELIV_PER="-",
            OPEN_PRICE="-", HIGH_PRICE="-", LOW_PRICE="-", CLOSE_PRICE="-",
        ),
        "2026-11-06",
    )
    assert result["delivery_observation_counts"]["not_reported"] == 1


def test_rejects_partial_dash_delivery_fields_for_traded_security():
    with pytest.raises(ValueError, match="incomplete delivery quantity/percentage"):
        validate_delivery(
            _delivery_csv(DELIV_QTY="-", DELIV_PER="-"),
            "2026-11-06",
        )
