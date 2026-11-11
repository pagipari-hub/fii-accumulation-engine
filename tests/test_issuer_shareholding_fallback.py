import pytest

from fii_engine.issuer_shareholding_fallback import parse_abbott_pdf_text, parse_mcx_html


def test_abbott_official_pdf_extracts_category_i_and_ii():
    text = """
    Abbott India Limited
    Scrip code 500488
    ISIN INE358A01014
    Quarter Ended / Half year ended/Date of Report 30-09-2026
    Foreign Portfolio Investor Category I 53 78784 78784 0.37
    Foreign Portfolio Investor Category II 4 5316 5316 0.03
    """
    parsed = parse_abbott_pdf_text(text)
    assert parsed["fpi_category_i_shares"] == 78784
    assert parsed["fpi_category_ii_shares"] == 5316
    assert parsed["fpi_total_shares"] == 84100
    assert parsed["fpi_total_ownership_percentage_points"] == "0.4"


def test_abbott_rejects_wrong_identity_or_quarter():
    with pytest.raises(ValueError, match="ISIN"):
        parse_abbott_pdf_text(
            "Abbott India Limited Scrip code 500488 ISIN WRONG 30-09-2026 "
            "Foreign Portfolio Investor Category I 1 1 1 0.01 "
            "Foreign Portfolio Investor Category II 1 1 1 0.01"
        )


def test_mcx_issuer_html_extracts_publisher_total_only():
    html = """
    <html><body>
      <h2>MCX (BSE: MCX)</h2>
      <h3>Shareholding Pattern as on September 30, 2026</h3>
      <table><tr><td>Foreign Portfolio Investors</td><td>69014370</td><td>27.07</td></tr></table>
    </body></html>
    """
    parsed = parse_mcx_html(html.encode())
    assert parsed["fpi_total_shares"] == 69014370
    assert parsed["fpi_total_ownership_percentage_points"] == "27.07"
    assert parsed["fpi_category_i_shares"] is None
    assert parsed["category_breakdown_status"] == "publisher_total_only_category_split_unavailable"


def test_mcx_rejects_missing_quarter_or_symbol():
    with pytest.raises(ValueError, match="does not identify MCX"):
        parse_mcx_html(
            b"<html><body>Shareholding Pattern as on September 30, 2026 "
            b"Foreign Portfolio Investors 100 1.0</body></html>"
        )
