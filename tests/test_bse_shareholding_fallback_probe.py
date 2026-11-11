import json

import pytest

from fii_engine.bse_shareholding_fallback_probe import _safe_attachment_url, select_quarter_rows


def test_select_quarter_rows_filters_exact_scripcode_and_quarter():
    payload = json.dumps({
        "Table": [
            {"FLD_ScripCode": 500488, "EndDate": "2026-06-30T00:00:00", "D": "2026-07-20", "IsXBRL": 1},
            {"FLD_ScripCode": 500488, "EndDate": "2026-09-30T00:00:00", "D": "2026-10-20", "IsXBRL": 1},
            {"FLD_ScripCode": 500488, "EndDate": "2026-09-30T00:00:00", "D": "2026-10-18", "IsXBRL": 1},
            {"FLD_ScripCode": 999999, "EndDate": "2026-09-30T00:00:00", "D": "2026-10-21", "IsXBRL": 1},
        ]
    }).encode()
    rows = select_quarter_rows(payload, {
        "reporting_date": "2026-09-30",
        "bse_scrip_code": "500488",
    })
    assert len(rows) == 2
    assert rows[0]["D"] == "2026-10-20"


def test_select_quarter_rows_rejects_non_json():
    with pytest.raises(ValueError, match="valid JSON"):
        select_quarter_rows(b"<html>not json</html>", {
            "reporting_date": "2026-09-30",
            "bse_scrip_code": "500488",
        })


def test_safe_attachment_url_requires_bse_xbrl_path():
    assert _safe_attachment_url("/XBRLFILES/SHPXBRLDataXML/500488_20261020_SP.html").startswith(
        "https://www.bseindia.com/XBRLFILES/"
    )
    with pytest.raises(ValueError, match="untrusted"):
        _safe_attachment_url("https://evil.example/XBRLFILES/a.html")
    with pytest.raises(ValueError, match="untrusted"):
        _safe_attachment_url("/corporates/other.html")
