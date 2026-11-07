import io
import json
import zipfile

import pytest

from fii_engine.nse_fo_bhavcopy import (
    REPORT_KEY,
    _read_zip,
    select_final_bhavcopy,
    validate_udiff_csv,
)

HEADERS = [
    "TradDt", "BizDt", "Sgmt", "Src", "FinInstrmTp", "FinInstrmId",
    "ISIN", "TckrSymb", "SctySrs", "XpryDt", "FininstrmActlXpryDt",
    "StrkPric", "OptnTp", "FinInstrmNm", "OpnPric", "HghPric", "LwPric",
    "ClsPric", "LastPric", "PrvsClsgPric", "UndrlygPric", "SttlmPric",
    "OpnIntrst", "ChngInOpnIntrst", "TtlTradgVol", "TtlTrfVal",
    "TtlNbOfTxsExctd", "SsnId", "NewBrdLotQty", "Rmks", "Rsvd1",
    "Rsvd2", "Rsvd3", "Rsvd4",
]


def _row(instrument_type="STF", instrument_id="1001", symbol="RELIANCE",
         strike="0.000000", option_type="XX"):
    values = {
        "TradDt": "2026-11-06", "BizDt": "2026-11-06", "Sgmt": "FO",
        "Src": "NSE", "FinInstrmTp": instrument_type,
        "FinInstrmId": instrument_id, "ISIN": "", "TckrSymb": symbol,
        "SctySrs": "", "XpryDt": "2026-11-26",
        "FininstrmActlXpryDt": "", "StrkPric": strike,
        "OptnTp": option_type, "FinInstrmNm": instrument_type,
        "OpnPric": "100.0", "HghPric": "105.0", "LwPric": "99.0",
        "ClsPric": "103.0", "LastPric": "103.0", "PrvsClsgPric": "101.0",
        "UndrlygPric": "2500.0", "SttlmPric": "103.0",
        "OpnIntrst": "12000", "ChngInOpnIntrst": "500",
        "TtlTradgVol": "2500", "TtlTrfVal": "257500.0",
        "TtlNbOfTxsExctd": "125", "SsnId": "F", "NewBrdLotQty": "250",
        "Rmks": "", "Rsvd1": "", "Rsvd2": "", "Rsvd3": "", "Rsvd4": "",
    }
    return values


def _csv(rows):
    return (",".join(HEADERS) + "\n" + "\n".join(
        ",".join(row.get(header, "") for header in HEADERS) for row in rows
    ) + "\n").encode()


def _catalogue_record(trading_date, filename):
    return {
        "fileKey": REPORT_KEY,
        "fileActlName": filename,
        "filePath": "https://nsearchives.nseindia.com/content/fo/",
        "tradingDate": trading_date,
        "displayName": "F&O-UDiFF Common Bhavcopy Final (zip)",
        "fileSize": "1.22 MB",
    }


def test_selects_latest_final_udiff_from_current_and_previous_days():
    catalogue = {
        "currentDate": "06-Nov-2026",
        "previousDate": "05-Nov-2026",
        "CurrentDay": [_catalogue_record(
            "06-Nov-2026", "BhavCopy_NSE_FO_0_0_0_20261106_F_0000.csv.zip"
        )],
        "PreviousDay": [_catalogue_record(
            "05-Nov-2026", "BhavCopy_NSE_FO_0_0_0_20261105_F_0000.csv.zip"
        )],
    }
    selected = select_final_bhavcopy(catalogue)
    assert selected["trading_date"] == "2026-11-06"
    assert selected["catalogue_group"] == "CurrentDay"
    assert selected["download_url"].endswith("20261106_F_0000.csv.zip")


def test_rejects_catalogue_filename_date_mismatch():
    catalogue = {
        "currentDate": "06-Nov-2026",
        "CurrentDay": [_catalogue_record(
            "06-Nov-2026", "BhavCopy_NSE_FO_0_0_0_20261105_F_0000.csv.zip"
        )],
        "PreviousDay": [],
    }
    with pytest.raises(ValueError, match="catalogue/file date mismatch"):
        select_final_bhavcopy(catalogue)


def test_validates_stock_futures_and_options_rows():
    rows = [
        _row("STF", "1001"),
        _row("STO", "1002", strike="2500.000000", option_type="CE"),
        _row("IDO", "1003", symbol="NIFTY", strike="25000.000000", option_type="PE"),
        _row("IDF", "1004", symbol="NIFTY"),
    ]
    result = validate_udiff_csv(_csv(rows), "2026-11-06")
    assert result["row_count"] == 4
    assert result["instrument_type_counts"] == {"IDF": 1, "IDO": 1, "STF": 1, "STO": 1}
    assert result["distinct_contract_ids"] == 4
    assert result["business_dates"] == ["2026-11-06"]


def test_rejects_missing_required_column():
    csv_bytes = _csv([_row()]).decode()
    csv_bytes = csv_bytes.replace(",NewBrdLotQty", "")
    with pytest.raises(ValueError, match="schema drift"):
        validate_udiff_csv(csv_bytes.encode(), "2026-11-06")


def test_rejects_trade_date_mismatch():
    with pytest.raises(ValueError, match="does not match catalogue date"):
        validate_udiff_csv(_csv([_row()]), "2026-11-05")


def test_rejects_wrong_segment_or_source():
    row = _row()
    row["Sgmt"] = "CM"
    with pytest.raises(ValueError, match="unexpected segment/source"):
        validate_udiff_csv(_csv([row]), "2026-11-06")


def test_rejects_unknown_instrument_type():
    with pytest.raises(ValueError, match="unknown FinInstrmTp"):
        validate_udiff_csv(_csv([_row("XYZ")]), "2026-11-06")


def test_rejects_duplicate_contract_identifiers():
    with pytest.raises(ValueError, match="duplicate FinInstrmId"):
        validate_udiff_csv(_csv([_row(), _row("STF", "1001", symbol="SBIN")]), "2026-11-06")


def test_rejects_invalid_option_type():
    with pytest.raises(ValueError, match="invalid option type"):
        validate_udiff_csv(
            _csv([_row("STO", "1002", strike="2500", option_type="XX")]),
            "2026-11-06",
        )


def test_rejects_non_numeric_oi():
    row = _row()
    row["OpnIntrst"] = "unknown"
    with pytest.raises(ValueError, match="non-numeric OpnIntrst"):
        validate_udiff_csv(_csv([row]), "2026-11-06")


def test_reads_single_csv_from_valid_zip():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("BhavCopy.csv", _csv([_row()]))
    name, payload = _read_zip(stream.getvalue())
    assert name == "BhavCopy.csv"
    assert payload == _csv([_row()])


def test_rejects_zip_with_multiple_files():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("one.csv", _csv([_row()]))
        archive.writestr("two.csv", _csv([_row()]))
    with pytest.raises(ValueError, match="expected one CSV"):
        _read_zip(stream.getvalue())


def test_rejects_empty_csv():
    with pytest.raises(ValueError, match="no data rows"):
        validate_udiff_csv((",".join(HEADERS) + "\n").encode(), "2026-11-06")
