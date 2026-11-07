from fii_engine.source_probe import discover_links


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
