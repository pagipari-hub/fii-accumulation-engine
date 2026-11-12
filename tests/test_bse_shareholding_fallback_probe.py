import csv
import hashlib
import json

import pytest

import fii_engine.bse_shareholding_fallback_probe as fallback
from fii_engine.bse_shareholding_fallback_probe import (
    _safe_attachment_url,
    probe,
    select_quarter_rows,
)


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


def test_api_is_attempted_when_bootstrap_page_returns_403(tmp_path, monkeypatch):
    udiff_path = tmp_path / "bse_udiff.csv"
    with udiff_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["TckrSymb", "FinInstrmId", "ISIN", "SctySrs"]
        )
        writer.writeheader()
        writer.writerow({
            "TckrSymb": "ABBOTINDIA",
            "FinInstrmId": "500488",
            "ISIN": "INE358A01014",
            "SctySrs": "A",
        })
    udiff_digest = hashlib.sha256(udiff_path.read_bytes()).hexdigest()

    candidates_path = tmp_path / "candidates.csv"
    fields = [
        "bse_udiff_source_sha256", "bse_symbol", "bse_scrip_code",
        "universe_isin", "bse_series", "symbol", "reporting_date",
    ]
    with candidates_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow({
            "bse_udiff_source_sha256": udiff_digest,
            "bse_symbol": "ABBOTINDIA",
            "bse_scrip_code": "500488",
            "universe_isin": "INE358A01014",
            "bse_series": "A",
            "symbol": "ABBOTINDIA",
            "reporting_date": "2026-09-30",
        })

    calls = []

    def fake_get(url, accept, referer=fallback.REFERER):
        calls.append(url)
        if url == fallback.REFERER:
            raise RuntimeError("BSE request failed (403): bootstrap")
        return b'{"Table":[]}', {
            "http_status": 200,
            "content_type": "application/json",
            "response_url": url,
        }

    monkeypatch.setattr(fallback, "_get", fake_get)
    report = probe(candidates_path, tmp_path / "out", udiff_path)

    assert calls[0] == fallback.REFERER
    assert len(calls) == 2
    assert calls[1].startswith(fallback.API_BASE)
    assert report["observations"][0]["status"] == "no_quarter_filing"
    assert report["api_access_status"] == "responses_received"
