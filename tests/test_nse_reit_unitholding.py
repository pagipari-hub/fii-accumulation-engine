from decimal import Decimal

import pytest

from fii_engine.nse_reit_unitholding import parse_fpi_unitholding_rows


def test_sums_separate_sponsor_and_public_fpi_rows():
    parsed = parse_fpi_unitholding_rows(
        """
        (d) Foreign Portfolio Investors 33,87,637 0.41 33,87,637 100.00 - -
        (g) Foreign Portfolio Investors 8,50,05,402 10.24
        """
    )
    assert parsed["fpi_units"] == 8_83_93_039
    assert Decimal(parsed["fpi_ownership_percentage_points"]) == Decimal("10.65")
    assert parsed["reported_fpi_row_count"] == 2


def test_explicit_publisher_dash_is_zero_but_missing_row_is_not():
    parsed = parse_fpi_unitholding_rows(
        """
        (d) Foreign Portfolio Investors - - - - - -
        (g) Foreign Portfolio Investors 74,86,373 0.22
        """
    )
    assert parsed["fpi_units"] == 7_486_373
    assert Decimal(parsed["fpi_ownership_percentage_points"]) == Decimal("0.22")


def test_rejects_partial_dash_and_unparseable_missing_category():
    with pytest.raises(ValueError, match="partial dash"):
        parse_fpi_unitholding_rows("Foreign Portfolio Investors - 0.22")
    with pytest.raises(ValueError, match="no parseable"):
        parse_fpi_unitholding_rows("Foreign Portfolio Investor 100 1.0")


def test_rejects_fpi_percentage_above_100():
    with pytest.raises(ValueError, match="outside valid range"):
        parse_fpi_unitholding_rows("Foreign Portfolio Investors 100 101.0")
