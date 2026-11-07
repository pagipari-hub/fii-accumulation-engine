from fii_engine.source_probe import (
    discover_links,
    discover_script_urls,
    extract_endpoint_candidates,
)


def test_discover_links_finds_file_suffixes_and_report_links():
    html = """
    <html><body>
      <a href="/files/constituents.csv">Constituents</a>
      <a href="/files/final.zip?download=1">Final Bhavcopy</a>
      <a href="/reports/participant-wise">Participant-wise Open Interest</a>
      <a href="/about">About us</a>
      <a href="/files/constituents.csv">Duplicate</a>
    </body></html>
    """
    links = discover_links(html)
    assert [item["href"] for item in links] == [
        "/files/constituents.csv",
        "/files/final.zip?download=1",
        "/reports/participant-wise",
    ]


def test_empty_page_has_no_candidates():
    assert discover_links("<html><body><a href='/about'>About</a></body></html>") == []


def test_script_discovery_resolves_only_same_origin_assets():
    html = """
    <script src="/assets/app.js"></script>
    <script src="https://www.nseindia.com/assets/reports.js"></script>
    <script src="https://cdn.example.com/vendor.js"></script>
    """
    assert discover_script_urls(html, "https://www.nseindia.com/all-reports-derivatives") == [
        "https://www.nseindia.com/assets/app.js",
        "https://www.nseindia.com/assets/reports.js",
    ]


def test_endpoint_extraction_finds_relevant_api_paths_and_deduplicates():
    source = '''
      const reportUrl = "/api/reports/download?name=fo";
      const oldUrl = "/about";
      const other = "https:\\/\\/www.nseindia.com/api/market-data";
      const duplicate = "/api/reports/download?name=fo";
    '''
    candidates = extract_endpoint_candidates(source)
    assert "/api/reports/download?name=fo" in candidates
    assert "https://www.nseindia.com/api/market-data" in candidates
    assert "/about" not in candidates
    assert len(candidates) == 2


def test_equities_reports_page_is_in_official_source_probe():
    from fii_engine.source_probe import SOURCE_PAGES

    assert SOURCE_PAGES["nse_equities_reports"] == "https://www.nseindia.com/all-reports"
