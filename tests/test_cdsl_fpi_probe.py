from urllib.parse import parse_qs

from fii_engine.cdsl_fpi_probe import _post_archive_date, summarize_html


def test_summary_parser_extracts_tables_and_archive_links():
    payload = b"""
    <html><body>
      <table><tr><th>Date</th><th>Net</th></tr>
      <tr><td>06-NOV-2026</td><td>123.45</td></tr></table>
      <a href="/downloads/sample.zip">November archive</a>
      <input type="hidden" name="__VIEWSTATE" value="state-token" />
      <input type="hidden" name="__EVENTVALIDATION" value="validation-token" />
      <input type="radio" name="grpArchive" value="rdbAfter" checked="checked" />
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
    assert result["input_count"] == 3
    assert result["inputs"][2]["checked"] == "true"


def test_summary_parser_handles_empty_page():
    assert summarize_html(b"<html><body>empty</body></html>") == {
        "table_count": 0,
        "tables": [],
        "link_count": 0,
        "links": [],
        "input_count": 0,
        "inputs": [],
    }


def test_archive_post_uses_captured_form_state_and_checks_requested_date(tmp_path):
    class FakeResponse:
        status = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"<table><tr><td>06-NOV-2026</td></tr></table>"

    class FakeOpener:
        def open(self, request, timeout):
            assert request.get_method() == "POST"
            fields = parse_qs(request.data.decode("utf-8"))
            assert fields["__VIEWSTATE"] == ["state-token"]
            assert fields["__EVENTVALIDATION"] == ["validation-token"]
            assert fields["grpArchive"] == ["rdbAfter"]
            assert fields["selectedDate"] == ["November 6, 2026"]
            return FakeResponse()

    archive_html = b"""
    <form>
      <input type="hidden" name="__VIEWSTATE" value="state-token" />
      <input type="hidden" name="__EVENTVALIDATION" value="validation-token" />
      <input type="radio" name="grpArchive" value="rdbAfter" checked="checked" />
      <input type="text" name="selectedDate" value="" />
      <input type="submit" name="ctl30" value="Go" />
    </form>
    """
    result = _post_archive_date(
        FakeOpener(),
        "https://www.cdslindia.com/Publications/FIITrends.aspx",
        archive_html,
        "November 6, 2026",
        "06-NOV-2026",
        tmp_path,
    )
    assert result["status"] == "success"
    assert result["response_date_match"] is True
    assert (tmp_path / "archive_2026-11-06.html").exists()
