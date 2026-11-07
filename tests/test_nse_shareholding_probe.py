import json

import pytest

from fii_engine.nse_shareholding_probe import _json_contract, _rss_contract


def test_json_contract_inspects_array_master_rows():
    payload = json.dumps([
        {"symbol": "ABC", "date": "30-Sep-2026", "recordId": "1"},
        {"symbol": "XYZ", "date": "30-Sep-2026", "recordId": "2"},
    ]).encode()
    result = _json_contract(payload)
    assert result["top_level_type"] == "list"
    assert result["row_count"] == 2
    assert result["first_row_keys"] == ["date", "recordId", "symbol"]


def test_json_contract_inspects_schema_columns_and_hover_table():
    payload = json.dumps({
        "columns": [{"title": "Company", "field": "companyName"}],
        "hoverTable": [{"title": "FPI", "field": "fpiHolding"}],
    }).encode()
    result = _json_contract(payload)
    assert result["columns_count"] == 1
    assert result["hoverTable_count"] == 1


def test_rss_contract_extracts_item_dates_and_links():
    payload = b"""<?xml version="1.0"?>
    <rss version="2.0"><channel><item>
      <title>ABC Limited - Shareholding Pattern</title>
      <link>https://example.invalid/filing</link>
      <pubDate>Fri, 06 Nov 2026 18:00:00 +0530</pubDate>
      <guid>record-1</guid><description>Quarterly filing</description>
    </item></channel></rss>"""
    result = _rss_contract(payload)
    assert result["item_count"] == 1
    assert result["sample_items"][0]["guid"] == "record-1"


def test_rss_contract_rejects_invalid_xml():
    with pytest.raises(ValueError, match="valid XML"):
        _rss_contract(b"<rss><item>")
