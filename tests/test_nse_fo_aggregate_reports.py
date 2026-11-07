import pytest

from fii_engine.nse_fo_aggregate_reports import (
    PARTICIPANT_COLUMNS,
    capture_dataset,
    inspect_csv,
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


def _participant_csv(dataset="participant_oi", mismatch=False):
    title_type = "Open Interest" if dataset == "participant_oi" else "Trading Volume"
    title = (
        f'""Participant wise {title_type} (no. of contracts) in Equity Derivatives '
        'as on Nov 06, 2026"",,,,,,,,,,,,,,'
    )
    rows = []
    for category, base in (("Client", 1), ("DII", 2), ("FII", 3), ("Pro", 4)):
        components = [base] * 12
        long_total = sum(components[i] for i in (0, 2, 4, 5, 8, 9))
        short_total = sum(components[i] for i in (1, 3, 6, 7, 10, 11))
        rows.append([category, *components, long_total, short_total])
    total_components = [
        sum(row[i + 1] for row in rows) for i in range(12)
    ]
    total_long = sum(total_components[i] for i in (0, 2, 4, 5, 8, 9))
    total_short = sum(total_components[i] for i in (1, 3, 6, 7, 10, 11))
    if mismatch:
        total_components[4] += 1
    rows.append(["TOTAL", *total_components, total_long, total_short])
    lines = [title, ",".join(PARTICIPANT_COLUMNS)]
    lines.extend(",".join(str(value) for value in row) for row in rows)
    return ("\n".join(lines) + "\n").encode()


def test_inspects_real_nse_participant_csv_structure_and_reconciles_totals():
    result = inspect_csv(_participant_csv(), "participant_oi", "2026-11-06")
    assert result["row_count"] == 5
    assert result["column_count"] == 15
    assert result["trade_date_in_title"] == "2026-11-06"
    assert result["units_as_labelled"] == "number_of_contracts"
    assert result["participant_categories"] == ["Client", "DII", "FII", "Pro", "TOTAL"]
    assert result["reconciliation"]["status"] == "exact_match"
    assert result["reconciliation"]["max_absolute_difference"] == 0


def test_records_small_source_total_discrepancies_without_rewriting_them():
    result = inspect_csv(_participant_csv(mismatch=True), "participant_oi", "2026-11-06")
    assert result["reconciliation"]["status"] == "difference_observed"
    assert result["reconciliation"]["column_totals"]["Option Index Call Long"]["difference"] == 1


def test_rejects_participant_report_title_date_mismatch():
    with pytest.raises(ValueError, match="does not match catalogue date"):
        inspect_csv(_participant_csv(), "participant_oi", "2026-11-05")


def test_rejects_wrong_participant_report_type_in_title():
    with pytest.raises(ValueError, match="unexpected report title"):
        inspect_csv(_participant_csv("participant_volume"), "participant_oi", "2026-11-06")


def test_rejects_participant_csv_schema_drift():
    payload = _participant_csv().decode().replace("Future Index Long", "Index Future Long").encode()
    with pytest.raises(ValueError, match="schema drift"):
        inspect_csv(payload, "participant_oi", "2026-11-06")

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
