from fii_engine.nse_shareholding_unitholding_announcement_probe import (
    _attachment_urls,
    _is_pattern_announcement,
)


def test_matches_shareholding_and_unitholding_disclosures():
    assert _is_pattern_announcement({"desc": "Shareholding Pattern"})
    assert _is_pattern_announcement({"attchmntText": "Disclosure of Unitholding pattern for Q2"})
    assert not _is_pattern_announcement({"desc": "Outcome of Board Meeting"})


def test_extracts_official_attachment_and_xbrl_urls_only():
    row = {
        "attchmntFile": "https://nsearchives.nseindia.com/corporate/holding.pdf",
        "xbrlFile": "https://nsearchives.nseindia.com/corporate/xbrl/holding.xml",
        "otherUrl": "https://example.com/not-an-attachment",
        "hasXbrl": True,
    }
    assert _attachment_urls(row) == [
        "https://nsearchives.nseindia.com/corporate/holding.pdf",
        "https://nsearchives.nseindia.com/corporate/xbrl/holding.xml",
    ]
