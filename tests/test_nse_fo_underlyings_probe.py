from fii_engine.nse_fo_underlyings_probe import parse_underlying_tables


def test_parse_underlying_tables_extracts_current_individual_stock_symbols():
    html = """
    <table>
      <tr><th>S. NO.</th><th>UNDERLYING</th><th>SYMBOL</th></tr>
      <tr><td>1</td><td>HDFC BANK LIMITED</td><td>HDFCBANK</td></tr>
      <tr><td>2</td><td>ICICI BANK LIMITED</td><td>ICICIBANK</td></tr>
    </table>
    """
    result = parse_underlying_tables(html)
    assert result["total_underlying_rows"] == 2
    assert [row[-1] for row in result["underlying_tables"][0]["data_rows"]] == [
        "HDFCBANK", "ICICIBANK"
    ]


def test_parse_underlying_tables_returns_empty_when_page_is_not_server_rendered():
    result = parse_underlying_tables("<html><body><h1>List of Underlyings</h1></body></html>")
    assert result["total_underlying_rows"] == 0
    assert result["underlying_tables"] == []
