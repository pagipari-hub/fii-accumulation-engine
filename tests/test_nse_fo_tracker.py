from datetime import date
from io import BytesIO

import pytest
from openpyxl import Workbook

from fii_engine.nse_fo_tracker import (
    _get_tracker_workbook,
    find_tracker_url,
    inspect_workbook,
    parse_tracker_events,
)


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


def test_parse_tracker_events_propagates_merged_effective_dates_and_circulars():
    workbook = Workbook()
    intro = workbook.active
    intro.title = "Introduction"
    intro.append([None, "Sr No", "Symbol", "Security Name", "Effective Date", "Circular Link"])
    intro.append([None, 1, "ABC", "ABC Limited", "November 29, 2024", "https://nsearchives.nseindia.com/a.pdf"])
    intro.append([None, 2, "XYZ", "XYZ Limited", None, None])
    intro.merge_cells("E2:E3")
    intro.merge_cells("F2:F3")

    exclusion = workbook.create_sheet("Exclusion")
    exclusion.append([None, "Sr No", "Symbol", "Security Name", "Effective Date", "Circular Link"])
    exclusion.append([None, 1, "PQR", "PQR Limited", "November 25, 2026", "https://nsearchives.nseindia.com/b.pdf"])

    output = BytesIO()
    workbook.save(output)
    workbook.close()

    result = parse_tracker_events(output.getvalue(), date(2026, 11, 7))
    assert result["summary"]["introduction"]["effective_as_of_date"] == 2
    assert result["summary"]["exclusion"]["future_scheduled"] == 1
    assert result["events"][1]["effective_date"] == "2024-11-29"
    assert result["events"][1]["circular_links"] == ["https://nsearchives.nseindia.com/a.pdf"]
    assert result["baseline_required"] is True


def test_tracker_download_retries_transient_html_response(monkeypatch):
    responses = iter([
        b"<!doctype html><title>Service Temporarily Unavailable</title>",
        b"PK\\x03\\x04workbook",
    ])
    calls = []

    def fake_get(*args, **kwargs):
        calls.append(args[0])
        return next(responses)

    monkeypatch.setattr("fii_engine.nse_fo_tracker._get", fake_get)
    monkeypatch.setattr("fii_engine.nse_fo_tracker.time.sleep", lambda _: None)

    payload, error = _get_tracker_workbook(
        "https://nsearchives.nseindia.com/tracker.xlsx",
        "https://www.nseindia.com",
        attempts=3,
    )

    assert payload.startswith(b"PK")
    assert error is None
    assert len(calls) == 2


def test_tracker_download_preserves_last_transient_html_response(monkeypatch):
    payload = b"<!doctype html><title>Service Temporarily Unavailable</title>"
    monkeypatch.setattr("fii_engine.nse_fo_tracker._get", lambda *args, **kwargs: payload)
    monkeypatch.setattr("fii_engine.nse_fo_tracker.time.sleep", lambda _: None)

    captured, error = _get_tracker_workbook(
        "https://nsearchives.nseindia.com/tracker.xlsx",
        "https://www.nseindia.com",
        attempts=2,
    )

    assert captured == payload
    assert error == "NSE tracker endpoint returned non-XLSX content"
