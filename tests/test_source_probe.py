from fii_engine.source_probe import (
    discover_links,
    discover_script_urls,
    extract_endpoint_candidates,
    extract_endpoint_contexts,
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


def test_endpoint_contexts_capture_bounded_reviewable_source():
    source = 'const x = "/api/daily-reports?key=" + reportKey + "&date=" + date;'
    contexts = extract_endpoint_contexts(source, radius=20)
    assert len(contexts) == 1
    assert contexts[0]["endpoint"] == "/api/daily-reports?key="
    assert "reportKey" in contexts[0]["context"]
    assert len(contexts[0]["context"]) <= len(source)


def test_official_stock_fo_reference_pages_are_probed():
    from fii_engine.source_probe import SOURCE_PAGES

    assert SOURCE_PAGES["nse_fo_contract_information"] == (
        "https://www.nseindia.com/static/products-services/equity-derivatives-contract-information"
    )
    assert SOURCE_PAGES["nse_fo_individual_securities"] == (
        "https://www.nseindia.com/static/products-services/equity-derivatives-individual-securities"
    )


def test_official_bulk_block_deals_page_is_probed():
    from fii_engine.source_probe import SOURCE_PAGES

    assert SOURCE_PAGES["nse_bulk_block_deals"] == (
        "https://www.nseindia.com/report-detail/display-bulk-and-block-deals"
    )


def test_keyword_contexts_capture_date_format_helpers():
    from fii_engine.source_probe import extract_keyword_contexts

    source = "function getStartEndDate(range) { return formatDate(new Date()); }"
    contexts = extract_keyword_contexts(source, keywords=("getStartEndDate",), radius=40)
    assert len(contexts) == 1
    assert contexts[0]["keyword"] == "getStartEndDate"
    assert "formatDate" in contexts[0]["context"]


def test_official_shareholding_pattern_page_is_probed():
    from fii_engine.source_probe import SOURCE_PAGES

    assert SOURCE_PAGES["nse_shareholding_pattern"] == (
        "https://www.nseindia.com/companies-listing/corporate-filings-shareholding-pattern/"
    )


def test_shareholding_and_xbrl_endpoints_are_discovered():
    from fii_engine.source_probe import extract_endpoint_candidates

    source = (
        'fetch("/api/shareholding-pattern?symbol=ABC"); '
        'fetch("/api/xbrl/ABC/2026Q2");'
    )
    candidates = extract_endpoint_candidates(source)
    assert "/api/shareholding-pattern?symbol=ABC" in candidates
    assert "/api/xbrl/ABC/2026Q2" in candidates


def test_keyword_contexts_reserve_matches_for_later_terms():
    from fii_engine.source_probe import extract_keyword_contexts

    source = ("shareholder " * 20) + "CFRefresh('ShareholderPattern')"
    contexts = extract_keyword_contexts(
        source, keywords=("shareholder", "CFRefresh"), radius=30, limit=20
    )
    assert any(item["keyword"] == "CFRefresh" for item in contexts)
