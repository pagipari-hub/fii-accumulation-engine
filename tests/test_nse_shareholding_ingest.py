import csv
import json
from pathlib import Path

from fii_engine import nse_shareholding_ingest as module


def _xbrl(i_shares: int, ii_shares: int, other_shares: int) -> bytes:
    foreign_total = i_shares + ii_shares + other_shares
    i_pct = i_shares / 1000
    ii_pct = ii_shares / 1000
    other_pct = other_shares / 1000
    foreign_pct = foreign_total / 1000
    contexts = []
    facts = []
    members = [
        ("I", "InstitutionsForeignPortfolioInvestorCategoryOneMember", i_shares, i_pct),
        ("II", "InstitutionsForeignPortfolioInvestorCategoryTwoMember", ii_shares, ii_pct),
        ("OTHER", "OtherInstitutionsForeignMember", other_shares, other_pct),
        ("FOREIGN", "InstitutionsForeignMember", foreign_total, foreign_pct),
    ]
    for context_id, member, shares, pct in members:
        contexts.append(
            f'<xbrli:context id="{context_id}"><xbrli:entity><xbrli:identifier scheme="test">500180</xbrli:identifier></xbrli:entity>'
            f'<xbrli:period><xbrli:instant>2026-09-30</xbrli:instant></xbrli:period>'
            f'<xbrli:scenario><xbrli:explicitMember dimension="shp:CategoryOfShareholdersAxis">shp:{member}</xbrli:explicitMember></xbrli:scenario></xbrli:context>'
        )
        facts.append(
            f'<shp:NumberOfShares contextRef="{context_id}" unitRef="shares">{shares}</shp:NumberOfShares>'
            f'<shp:ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="{context_id}" unitRef="pure">{pct}</shp:ShareholdingAsAPercentageOfTotalNumberOfShares>'
        )
    xml = (
        '<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" '
        'xmlns:shp="https://example.invalid/shareholding">'
        + "".join(contexts)
        + '<xbrli:unit id="shares"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>'
        + '<xbrli:unit id="pure"><xbrli:measure>xbrli:pure</xbrli:measure></xbrli:unit>'
        + "".join(facts)
        + "</xbrli:xbrl>"
    )
    return xml.encode()


def _master_row(record_id, symbol, name, isin, broadcast, revised, xbrl_url):
    return {
        "recordId": str(record_id),
        "symbol": symbol,
        "name": name,
        "isin": isin,
        "date": "30-SEP-2026",
        "submissionDate": "06-OCT-2026",
        "broadcastDate": broadcast,
        "systemDate": broadcast,
        "revisedData": revised,
        "revisionDate": "20-OCT-2026" if revised == "Revised" else None,
        "revisionRemark": "revised filing" if revised == "Revised" else None,
        "xbrl": xbrl_url,
    }


def _setup(tmp_path: Path):
    universe_path = tmp_path / "data/raw/universe/nifty500_current.csv"
    universe_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"symbol": "ABC", "company_name": "ABC Limited", "industry": "Test", "series": "EQ", "isin": "INE000A01001"},
        {"symbol": "XYZ", "company_name": "XYZ Limited", "industry": "Test", "series": "EQ", "isin": "INE000B01001"},
        {"symbol": "DEF", "company_name": "DEF Limited", "industry": "Test", "series": "EQ", "isin": "INE000C01001"},
    ]
    with universe_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    urls = {
        "abc_old": "https://nsearchives.nseindia.com/corporate/xbrl/abc_old.xml",
        "abc_new": "https://nsearchives.nseindia.com/corporate/xbrl/abc_new.xml",
        "xyz": "https://nsearchives.nseindia.com/corporate/xbrl/xyz.xml",
    }
    master = [
        _master_row("101", "ABC", "ABC Limited", "INE000A01001", "06-OCT-2026 17:00:00", "N", urls["abc_old"]),
        _master_row("102", "ABC", "ABC Limited", "INE000A01001", "20-OCT-2026 17:00:00", "Revised", urls["abc_new"]),
        _master_row("201", "XYZ", "XYZ Limited", None, "08-OCT-2026 17:00:00", "N", urls["xyz"]),
        _master_row("301", "DEF", "DEF Limited", "WRONGISIN", "09-OCT-2026 17:00:00", "N",
                    "https://nsearchives.nseindia.com/corporate/xbrl/def.xml"),
    ]
    payloads = {
        urls["abc_old"]: _xbrl(100, 50, 25),
        urls["abc_new"]: _xbrl(150, 50, 25),
        urls["xyz"]: _xbrl(300, 100, 50),
    }
    return rows, master, payloads, urls


def test_ingests_fpi_categories_and_selects_latest_revision(tmp_path, monkeypatch):
    _, master, payloads, urls = _setup(tmp_path)
    calls = []

    def fake_get(url, referer, accept):
        calls.append(url)
        if url == module.PAGE:
            return b"<html>ok</html>", {"http_status": 200, "content_type": "text/html"}
        if url == module.MASTER_URL:
            return json.dumps(master).encode(), {"http_status": 200, "content_type": "application/json"}
        if url in payloads:
            return payloads[url], {"http_status": 200, "content_type": "application/xml"}
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(module, "_get", fake_get)
    result = module.ingest(tmp_path)

    assert result["status"] == "partial_identity_coverage"
    assert result["eligible_filing_count"] == 3
    assert result["fetched_filing_count"] == 3
    assert result["fetch_error_count"] == 0
    assert result["latest_quarter_parsed_count"] == 2
    assert result["latest_quarter_coverage_pct"] == 66.67

    ownership_path = tmp_path / result["ownership_current_path"]
    with ownership_path.open(newline="", encoding="utf-8") as handle:
        rows = {row["symbol"]: row for row in csv.DictReader(handle)}
    assert rows["ABC"]["record_id"] == "102"
    assert rows["ABC"]["revised_data"] == "Revised"
    assert rows["ABC"]["fpi_category_i_shares"] == "150"
    assert rows["ABC"]["fpi_category_ii_shares"] == "50"
    assert rows["ABC"]["fpi_total_shares"] == "200"
    assert rows["ABC"]["fpi_total_ownership_percentage_points"] == "20.00"
    assert rows["ABC"]["fpi_reconciliation_status"] == "matched"
    assert rows["XYZ"]["identity_join_method"] == "symbol_and_company_name"
    assert rows["XYZ"]["filing_status"] == "parsed"
    assert rows["DEF"]["identity_status"] == "isin_mismatch"
    assert rows["DEF"]["filing_status"] == "isin_mismatch"
    assert len(list((tmp_path / "data/snapshots/shareholding_filings").glob("filing_*.json"))) == 3
    assert len(list((tmp_path / "data/raw/shareholding/xbrl").glob("*.xml"))) == 3


def test_reuses_immutable_filing_snapshots_without_refetching_xbrl(tmp_path, monkeypatch):
    _, master, payloads, _ = _setup(tmp_path)
    xbrl_calls = []

    def fake_get(url, referer, accept):
        if url == module.PAGE:
            return b"<html>ok</html>", {"http_status": 200, "content_type": "text/html"}
        if url == module.MASTER_URL:
            return json.dumps(master).encode(), {"http_status": 200, "content_type": "application/json"}
        if url in payloads:
            xbrl_calls.append(url)
            return payloads[url], {"http_status": 200, "content_type": "application/xml"}
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(module, "_get", fake_get)
    first = module.ingest(tmp_path)
    first_snapshot = first["master_snapshot_path"]
    xbrl_calls.clear()
    second = module.ingest(tmp_path)

    assert first["master_snapshot_path"] == first_snapshot
    assert second["fetched_filing_count"] == 0
    assert second["reused_filing_count"] == 3
    assert xbrl_calls == []
    assert second["ownership_snapshot_path"] == first["ownership_snapshot_path"]


def test_does_not_fetch_untrusted_xbrl_hosts(tmp_path, monkeypatch):
    _, master, payloads, _ = _setup(tmp_path)
    master[-1]["isin"] = "INE000C01001"
    master[-1]["xbrl"] = "https://example.invalid/def.xml"
    calls = []

    def fake_get(url, referer, accept):
        calls.append(url)
        if url == module.PAGE:
            return b"<html>ok</html>", {"http_status": 200}
        if url == module.MASTER_URL:
            return json.dumps(master).encode(), {"http_status": 200}
        if url in payloads:
            return payloads[url], {"http_status": 200}
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(module, "_get", fake_get)
    result = module.ingest(tmp_path)
    assert any(item["status"] == "untrusted_xbrl_url" for item in result["fetch_errors"])
    assert "https://example.invalid/def.xml" not in calls
