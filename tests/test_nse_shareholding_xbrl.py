import json

import pytest

from fii_engine.nse_shareholding_xbrl import parse_xbrl


XML = b"""<?xml version="1.0"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
 xmlns:shp="https://example.invalid/shareholding">
 <xbrli:context id="FPI1"><xbrli:entity><xbrli:identifier scheme="test">INE000A01001</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:instant>2026-09-30</xbrli:instant></xbrli:period>
  <xbrli:scenario><xbrli:explicitMember dimension="shp:CategoryOfShareholdersAxis">shp:InstitutionsForeignPortfolioInvestorCategoryOneMember</xbrli:explicitMember></xbrli:scenario>
 </xbrli:context>
 <xbrli:context id="FPI2"><xbrli:entity><xbrli:identifier scheme="test">INE000A01001</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:instant>2026-09-30</xbrli:instant></xbrli:period>
  <xbrli:scenario><xbrli:explicitMember dimension="shp:CategoryOfShareholdersAxis">shp:InstitutionsForeignPortfolioInvestorCategoryTwoMember</xbrli:explicitMember></xbrli:scenario>
 </xbrli:context>
 <xbrli:context id="OTHER"><xbrli:entity><xbrli:identifier scheme="test">INE000A01001</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:instant>2026-09-30</xbrli:instant></xbrli:period>
  <xbrli:scenario><xbrli:explicitMember dimension="shp:CategoryOfShareholdersAxis">shp:OtherInstitutionsForeignMember</xbrli:explicitMember></xbrli:scenario>
 </xbrli:context>
 <xbrli:context id="FOREIGN"><xbrli:entity><xbrli:identifier scheme="test">INE000A01001</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:instant>2026-09-30</xbrli:instant></xbrli:period>
  <xbrli:scenario><xbrli:explicitMember dimension="shp:CategoryOfShareholdersAxis">shp:InstitutionsForeignMember</xbrli:explicitMember></xbrli:scenario>
 </xbrli:context>
 <xbrli:unit id="shares"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>
 <xbrli:unit id="pure"><xbrli:measure>xbrli:pure</xbrli:measure></xbrli:unit>
 <shp:NumberOfShares contextRef="FPI1" unitRef="shares">700</shp:NumberOfShares>
 <shp:NumberOfShareholders contextRef="FPI1" unitRef="pure">7</shp:NumberOfShareholders>
 <shp:ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="FPI1" unitRef="pure">0.07</shp:ShareholdingAsAPercentageOfTotalNumberOfShares>
 <shp:NumberOfShares contextRef="FPI2" unitRef="shares">200</shp:NumberOfShares>
 <shp:NumberOfShareholders contextRef="FPI2" unitRef="pure">2</shp:NumberOfShareholders>
 <shp:ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="FPI2" unitRef="pure">0.02</shp:ShareholdingAsAPercentageOfTotalNumberOfShares>
 <shp:NumberOfShares contextRef="OTHER" unitRef="shares">100</shp:NumberOfShares>
 <shp:ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="OTHER" unitRef="pure">0.01</shp:ShareholdingAsAPercentageOfTotalNumberOfShares>
 <shp:NumberOfShares contextRef="FOREIGN" unitRef="shares">1000</shp:NumberOfShares>
</xbrli:xbrl>"""


def test_extracts_fpi_categories_and_reconciles_foreign_institutions():
    result = parse_xbrl(XML)
    rows = {row["category_key"]: row for row in result["category_records"]}
    assert rows["fpi_category_i"]["shares"] == 700
    assert rows["fpi_category_i"]["ownership_fraction"] == "0.07"
    assert rows["fpi_category_i"]["ownership_percentage_points"] == "7.00"
    assert rows["fpi_category_ii"]["shares"] == 200
    assert rows["other_foreign_institutions"]["shares"] == 100
    assert result["fpi_reconciliation"][0]["status"] == "matched"
    assert result["fpi_reconciliation"][0]["component_sum_shares"] == 1000


def test_missing_fpi_category_is_not_assumed_to_be_zero():
    payload = XML.replace(
        b'<shp:NumberOfShares contextRef="FPI2" unitRef="shares">200</shp:NumberOfShares>',
        b'<shp:NumberOfShares contextRef="FPI2" unitRef="shares" xsi:nil="true"/>',
    )
    result = parse_xbrl(payload)
    rows = {row["category_key"]: row for row in result["category_records"]}
    assert rows["fpi_category_ii"]["shares"] is None
    assert result["fpi_reconciliation"][0]["status"] == "not_comparable_missing_component"


def test_rejects_conflicting_duplicate_fact_values():
    payload = XML.replace(
        b'</xbrli:xbrl>',
        b'<shp:NumberOfShares contextRef="FPI1" unitRef="shares">701</shp:NumberOfShares></xbrli:xbrl>',
    )
    with pytest.raises(ValueError, match="conflicting duplicate"):
        parse_xbrl(payload)


def test_rejects_non_fractional_percentage_outside_valid_range():
    payload = XML.replace(b">0.07</shp:ShareholdingAsAPercentageOfTotalNumberOfShares>",
                          b">7.0</shp:ShareholdingAsAPercentageOfTotalNumberOfShares>")
    with pytest.raises(ValueError, match="outside"):
        parse_xbrl(payload)


def test_rejects_invalid_xml():
    with pytest.raises(ValueError, match="valid XML"):
        parse_xbrl(b"<broken>")
