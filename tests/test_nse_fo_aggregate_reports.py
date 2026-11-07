import pytest

from fii_engine.nse_fo_aggregate_reports import (
    inspect_csv,
    capture_dataset,
    inspect_xls,
    select_report,
)


def _record(key, filename, trading_date, path="https://nsearchives.nseindia.com/content/nsccl/"):
    return {
        "fileKey": key,
        "fileActlName": filename,
        "filePath": path,
        "tradingDate": trading_date,
        "displayName": "NSE report",
        "fileSize": "1.00 KB",
    }


def _catalogue():
    return {
        "currentDate": "06-Nov-2026",
        "previousDate": "05-Nov-2026",
        "CurrentDay": [
            _record(
                "FO-PARTICIPANTWISE-OI",
                "fao_participant_oi_06112026.csv",
                "06-Nov-2026",
            ),
            _record(
                "FO-PARTICIPANTWISE-TRADING-VOL",
                "fao_participant_vol_06112026.csv",
                "06-Nov-2026",
            ),
            _record(
                "FO-FII-DERIVATIVE-STAT",
                "fii_stats_06-Nov-2026.xls",
                "06-Nov-2026",
                "https://nsearchives.nseindia.com/content/fo/",
            ),
        ],
        "PreviousDay": [
            _record(
                "FO-PARTICIPANTWISE-OI",
                "fao_participant_oi_05112026.csv",
                "05-Nov-2026",
            )
        ],
    }


def test_selects_latest_participant_oi_report_by_exact_catalogue_key():
    result = select_report(_catalogue(), "participant_oi")
    assert result["trading_date"] == "2026-11-06"
    assert result["filename"] == "fao_participant_oi_06112026.csv"
    assert result["attribution_scope"] == "participant_category_aggregate_not_stock_specific"


def test_selects_participant_volume_report():
    result = select_report(_catalogue(), "participant_volume")
    assert result["report_key"] == "FO-PARTICIPANTWISE-TRADING-VOL"
    assert result["download_url"].endswith("fao_participant_vol_06112026.csv")


def test_selects_aggregate_fii_derivatives_workbook():
    result = select_report(_catalogue(), "fii_derivatives_statistics")
    assert result["filename"] == "fii_stats_06-Nov-2026.xls"
    assert result["download_url"] == (
        "https://nsearchives.nseindia.com/content/fo/fii_stats_06-Nov-2026.xls"
    )
    assert result["attribution_scope"] == "aggregate_fii_derivatives_only"


def test_rejects_missing_report_key():
    catalogue = _catalogue()
    catalogue["CurrentDay"] = []
    catalogue["PreviousDay"] = []
    with pytest.raises(ValueError, match="has no FO-PARTICIPANTWISE-OI"):
        select_report(catalogue, "participant_oi")


def test_rejects_catalogue_filename_date_mismatch():
    catalogue = _catalogue()
    catalogue["CurrentDay"][0]["fileActlName"] = "fao_participant_oi_05112026.csv"
    with pytest.raises(ValueError, match="catalogue/filename date mismatch"):
        select_report(catalogue, "participant_oi")


def test_rejects_untrusted_archive_host():
    catalogue = _catalogue()
    catalogue["CurrentDay"][0]["filePath"] = "https://example.com/data/"
    with pytest.raises(ValueError, match="untrusted NSE archive path"):
        select_report(catalogue, "participant_oi")


def test_inspects_csv_header_and_rows_without_interpreting_values():
    payload = (
        "Participant,Index Futures Long,Index Futures Short,Stock Futures Long\n"
        "FII,10,20,30\n"
        "Client,40,50,60\n"
    ).encode()
    result = inspect_csv(payload, "participant_oi")
    assert result["row_count"] == 2
    assert result["column_count"] == 4
    assert result["columns"][0] == "Participant"
    assert len(result["preview_rows"]) == 2


def test_rejects_inconsistent_csv_row_width():
    payload = b"A,B,C\n1,2,3\n4,5\n"
    with pytest.raises(ValueError, match="row width differs"):
        inspect_csv(payload, "participant_volume")


def test_rejects_non_utf8_csv():
    with pytest.raises(ValueError, match="not UTF-8"):
        inspect_csv(b"\xff\xfe", "participant_oi")


def test_inspects_legacy_xls_sheet_metadata(monkeypatch):
    class Sheet:
        name = "FII Stats"
        nrows = 2
        ncols = 2

        @staticmethod
        def cell_value(row, col):
            return [["Instrument", "Buy"], ["Index Futures", 100]][row][col]

    class Book:
        @staticmethod
        def sheets():
            return [Sheet()]

        @staticmethod
        def release_resources():
            return None

    monkeypatch.setattr(
        "fii_engine.nse_fo_aggregate_reports.xlrd.open_workbook",
        lambda **kwargs: Book(),
    )
    payload = bytes.fromhex("D0CF11E0A1B11AE1") + b"test"
    result = inspect_xls(payload, "fii_derivatives_statistics")
    assert result["sheet_count"] == 1
    assert result["sheets"][0]["name"] == "FII Stats"
    assert result["sheets"][0]["preview_rows"][1] == ["Index Futures", 100]


def test_rejects_non_ole2_xls_payload():
    with pytest.raises(ValueError, match="not an OLE2"):
        inspect_xls(b"not an excel workbook", "fii_derivatives_statistics")



def test_preserves_original_csv_before_schema_inspection_failure(tmp_path, monkeypatch):
    from fii_engine import nse_fo_aggregate_reports as module

    raw = b"exchange source bytes that need inspection"
    monkeypatch.setattr(module, "_get", lambda *args, **kwargs: raw)
    monkeypatch.setattr(
        module,
        "inspect_csv",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("unrecognized preamble")),
    )
    result = capture_dataset("participant_oi", _catalogue(), tmp_path)
    raw_path = tmp_path / result["raw_path"]
    manifest_path = tmp_path / result["manifest_path"]
    assert raw_path.read_bytes() == raw
    assert result["inspection"] is None
    assert result["inspection_error"] == "unrecognized preamble"
    assert manifest_path.is_file()
