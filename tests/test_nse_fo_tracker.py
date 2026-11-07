from io import BytesIO

import pytest
from openpyxl import Workbook

from fii_engine.nse_fo_tracker import find_tracker_url, inspect_workbook


def test_find_tracker_url_uses_nse_link_text_and_resolves_relative_url():
    html = (
        '<a href="https://nsearchives.nseindia.com/files/tracker.xlsx">'
        'F&amp;O Stock Introduction and Exclusion Tracker</a>'
    )
    assert find_tracker_url(html).endswith("/files/tracker.xlsx")


def test_find_tracker_url_rejects_untrusted_host():
    html = (
        '<a href="https://example.com/tracker.xlsx">'
        'F&amp;O Stock Introduction and Exclusion Tracker</a>'
    )
    with pytest.raises(ValueError, match="untrusted"):
        find_tracker_url(html)


def test_find_tracker_url_rejects_ambiguous_matches():
    html = (
        '<a href="https://nsearchives.nseindia.com/a.xlsx">'
        'F&amp;O Stock Introduction and Exclusion Tracker</a>'
        '<a href="https://nsearchives.nseindia.com/b.xlsx">'
        'F&amp;O Stock Introduction and Exclusion Tracker</a>'
    )
    with pytest.raises(ValueError, match="exactly one"):
        find_tracker_url(html)


def test_inspect_workbook_extracts_sheet_names_and_preview_rows():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Tracker"
    sheet.append(["Symbol", "Action", "Effective Date"])
    sheet.append(["ABC", "Addition", "2026-11-02"])
    output = BytesIO()
    workbook.save(output)
    workbook.close()

    result = inspect_workbook(output.getvalue())
    assert result["sheet_names"] == ["Tracker"]
    assert result["sheets"][0]["preview_rows"][0] == [
        "Symbol", "Action", "Effective Date"
    ]


def test_inspect_workbook_rejects_non_xlsx_payload():
    with pytest.raises(ValueError, match="not an XLSX"):
        inspect_workbook(b"not a workbook")
