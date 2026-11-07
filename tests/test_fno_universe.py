from datetime import date

import pytest

from fii_engine.fno_universe import build_status_rows


def _nifty_rows():
    return [
        {"symbol": "AAA", "company_name": "AAA Ltd", "industry": "Banking", "series": "EQ", "isin": "INAAA"},
        {"symbol": "ZOMATO", "company_name": "Eternal Ltd", "industry": "Services", "series": "EQ", "isin": "INZOM"},
        {"symbol": "BBB", "company_name": "BBB Ltd", "industry": "IT", "series": "EQ", "isin": "INBBB"},
    ]


def test_builds_exhaustive_fno_status_and_applies_effective_symbol_alias():
    aliases = [{
        "old_symbol": "ZOMATO",
        "new_symbol": "ETERNAL",
        "effective_date": "2025-04-09",
    }]
    rows, summary = build_status_rows(
        _nifty_rows(), {"AAA", "ETERNAL"}, aliases, date(2026, 11, 7)
    )
    assert [row["fno_status"] for row in rows] == [
        "eligible", "eligible", "not_eligible"
    ]
    assert rows[1]["symbol_alias_applied"] == "true"
    assert rows[1]["matched_fno_symbol"] == "ETERNAL"
    assert summary == {
        "nifty500_count": 3,
        "eligible_count": 2,
        "not_eligible_count": 1,
        "unknown_count": 0,
        "alias_match_count": 1,
    }


def test_symbol_alias_is_not_used_before_effective_date():
    aliases = [{
        "old_symbol": "ZOMATO",
        "new_symbol": "ETERNAL",
        "effective_date": "2025-04-09",
    }]
    rows, _ = build_status_rows(
        _nifty_rows(), {"AAA", "ZOMATO"}, aliases, date(2025, 4, 8)
    )
    assert rows[1]["fno_status"] == "eligible"
    assert rows[1]["matched_fno_symbol"] == "ZOMATO"
    assert rows[1]["symbol_alias_applied"] == "false"


def test_rejects_duplicate_nifty500_symbols():
    rows = _nifty_rows()
    rows.append(dict(rows[0]))
    with pytest.raises(ValueError, match="duplicate symbols"):
        build_status_rows(rows, {"AAA"}, [], date(2026, 11, 7))


def test_rejects_missing_fno_source():
    with pytest.raises(ValueError, match="both required"):
        build_status_rows(_nifty_rows(), set(), [], date(2026, 11, 7))
