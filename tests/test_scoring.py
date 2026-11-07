import json
from pathlib import Path

import pytest

from fii_engine.scoring import load_config, score_stock, score_universe, validate_config


CONFIG = Path(__file__).parents[1] / "config" / "scoring.json"


@pytest.fixture
def config():
    return load_config(CONFIG)


def obs(family, score, *, status="observed", reason=None):
    return {
        "family": family,
        "status": status,
        "score": score,
        "reason": reason,
        "source_refs": ["fixture://verified-source"] if status == "observed" else [],
    }


def test_config_weights_sum_to_100_for_both_horizons(config):
    for definition in config["horizons"].values():
        assert sum(definition["family_weights"].values()) == 100


def test_missing_families_are_not_scored_as_zero(config):
    result = score_stock("ABC", [obs("cash_market", 0.8)], config)
    assert result["short_term"]["score"] == 80.0
    assert result["short_term"]["coverage"] == 25.0
    assert result["short_term"]["rankable"] is False
    assert "quarterly_ownership" in result["short_term"]["unavailable_families"]


def test_not_applicable_fno_family_does_not_reduce_coverage(config):
    observations = [
        obs("quarterly_ownership", 0.7),
        obs("disclosed_deals", 0.5),
        obs("cash_market", 0.8),
        obs("stock_derivatives", None, status="not_applicable", reason="not F&O eligible"),
        obs("participant_positioning", 0.6),
        obs("sector_relative_strength", 0.7),
    ]
    result = score_stock("CASHONLY", observations, config)
    assert result["short_term"]["coverage"] == 100.0
    assert result["short_term"]["not_applicable_families"] == ["stock_derivatives"]
    assert result["short_term"]["rankable"] is True


def test_unavailable_fno_family_reduces_coverage(config):
    observations = [
        obs("quarterly_ownership", 0.7),
        obs("disclosed_deals", 0.5),
        obs("cash_market", 0.8),
        obs("stock_derivatives", None, status="unavailable", reason="source missing"),
        obs("participant_positioning", 0.6),
        obs("sector_relative_strength", 0.7),
    ]
    result = score_stock("UNKNOWNFNO", observations, config)
    assert result["short_term"]["coverage"] == 70.0
    assert result["short_term"]["rankable"] is True


def test_grade_two_requires_three_independent_positive_families(config):
    observations = [
        obs("quarterly_ownership", 0.8),
        obs("cash_market", 0.9),
        obs("stock_derivatives", 0.75),
        obs("disclosed_deals", 0.2),
        obs("participant_positioning", 0.3),
        obs("sector_relative_strength", 0.4),
    ]
    result = score_stock("ABC", observations, config)
    assert result["evidence_grade"] == "Grade 2 - Corroborated"


def test_grade_one_requires_explicit_direct_transaction_flag(config):
    result = score_stock("ABC", [], config, direct_fpi_transaction=True)
    assert result["evidence_grade"] == "Grade 1 - Confirmed"
    assert result["short_term"]["score"] is None


def test_observed_zero_is_valid_but_requires_source_provenance(config):
    result = score_stock("ABC", [obs("cash_market", 0.0)], config)
    assert result["short_term"]["score"] == 0.0
    invalid = obs("cash_market", 0.0)
    invalid["source_refs"] = []
    with pytest.raises(ValueError, match="source_refs"):
        score_stock("ABC", [invalid], config)


def test_duplicate_or_unknown_family_is_rejected(config):
    with pytest.raises(ValueError, match="duplicate evidence family"):
        score_stock("ABC", [obs("cash_market", 0.2), obs("cash_market", 0.3)], config)
    with pytest.raises(ValueError, match="unknown evidence family"):
        score_stock("ABC", [obs("unknown", 0.2)], config)


def test_universe_keeps_every_symbol_and_only_ranks_eligible_rows(config):
    symbols = ["ABC", "XYZ", "NO_DATA"]
    evidence = {
        "ABC": [
            obs("quarterly_ownership", 0.8),
            obs("disclosed_deals", 0.7),
            obs("cash_market", 0.9),
            obs("stock_derivatives", 0.8),
            obs("participant_positioning", 0.7),
            obs("sector_relative_strength", 0.8),
        ],
        "XYZ": [
            obs("quarterly_ownership", 0.2),
            obs("disclosed_deals", 0.2),
            obs("cash_market", 0.4),
            obs("stock_derivatives", 0.3),
            obs("participant_positioning", 0.2),
            obs("sector_relative_strength", 0.2),
        ],
    }
    rows = score_universe(symbols, evidence, config)
    assert {row["symbol"] for row in rows} == set(symbols)
    abc = next(row for row in rows if row["symbol"] == "ABC")
    no_data = next(row for row in rows if row["symbol"] == "NO_DATA")
    assert abc["short_term_rank"] == 1
    assert no_data["short_term_rank"] is None
    assert no_data["short_term"]["score"] is None


def test_config_rejects_invalid_weight_totals(config):
    broken = json.loads(json.dumps(config))
    broken["horizons"]["short_term"]["family_weights"]["cash_market"] = 24
    with pytest.raises(ValueError, match="sum to 100"):
        validate_config(broken)
