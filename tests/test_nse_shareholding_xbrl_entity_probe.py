from fii_engine.nse_shareholding_xbrl_entity_probe import parse_xbrl_entity


def test_extracts_context_entity_identifier_and_company_name():
    payload = b"""<?xml version="1.0"?>
    <xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
                xmlns:in-ca="http://www.icai.org/">
      <xbrli:context id="c1">
        <xbrli:entity>
          <xbrli:identifier scheme="https://www.mca.gov.in">L12345MH2000PLC123456</xbrli:identifier>
        </xbrli:entity>
      </xbrli:context>
      <in-ca:NameOfCompany>Example Limited</in-ca:NameOfCompany>
    </xbrli:xbrl>"""
    result = parse_xbrl_entity(payload)
    assert result["entity_identifiers"] == [
        {"scheme": "https://www.mca.gov.in", "value": "L12345MH2000PLC123456"}
    ]
    assert result["company_name_facts"] == [
        {"tag": "NameOfCompany", "value": "Example Limited"}
    ]


def test_rejects_html_response():
    import pytest

    with pytest.raises(ValueError, match="HTML"):
        parse_xbrl_entity(b"<html>blocked</html>")


def test_rejects_xml_without_entity_identifier():
    import pytest

    with pytest.raises(ValueError, match="no entity identifier"):
        parse_xbrl_entity(b"<xbrl><fact>1</fact></xbrl>")
