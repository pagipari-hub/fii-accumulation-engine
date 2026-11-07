from fii_engine.cdsl_fpi_probe import summarize_html


def test_summary_parser_extracts_tables_and_archive_links():
    payload = b"""
    <html><body>
      <table><tr><th>Date</th><th>Net</th></tr>
      <tr><td>06-NOV-2026</td><td>123.45</td></tr></table>
      <a href="/downloads/sample.zip">November archive</a>
    </body></html>
    """
    result = summarize_html(payload)
    assert result["table_count"] == 1
    assert result["tables"][0][0] == ["Date", "Net"]
    assert result["tables"][0][1] == ["06-NOV-2026", "123.45"]
    assert result["links"] == [{
        "href": "/downloads/sample.zip",
        "text": "November archive",
    }]


def test_summary_parser_handles_empty_page():
    assert summarize_html(b"<html><body>empty</body></html>") == {
        "table_count": 0,
        "tables": [],
        "link_count": 0,
        "links": [],
    }
