import json
from pathlib import Path

import pytest

from fii_engine.scoring import load_config, score_stock, validate_config


CONFIG = Path(__file__).parents[1] / "config" / "scoring.json"


@pytest.fixture
def config():
    return load_config(CONFIG)


def observations(weights, values):
    return {
        key: (
            {"status": "observed", "score": values[key], "source_refs": [f"source:{key}"]}
            if key in values else
            {"status": "unavailable", "score": None, "reason": "source history unavailable"}
        )
        for key in weights
    }


def test_config_has_two_separate_100_point_scores(config):
    assert sum(item["weight"] for item in config["primary_score"]["dimensions"]) == 100
    assert sum(item["weight"] for item in config["confirmation_score"]["dimensions"]) == 100
    assert config["catalyst_assessment"]["display"] == "separate_bonus_not_added_to_fii_accumulation_score"


def test_fii_score_and_confirmation_are_separate(config):
    fii = observations(
        {item["key"]: item["weight"] for item in config["primary_score"]["dimensions"]},
        {
            "verified_net_fpi_transactions": 1.0,
            "persistence": 0.8,
            "quarterly_fpi_ownership_change": 0.6,
        },
    )
    confirmation = observations(
        {item["key"]: item["weight"] for item in config["confirmation_score"]["dimensions"]},
        {
            "delivery_strength": 1.0,
            "relative_price_strength": 0.5,
            "volume_confirmation": 0.5,
        },
    )
    result = score_stock(
        "ABC", fii, confirmation, config,
        identity_verified=True, direct_gate_passed=True,
        catalyst_assessment={"score": 6, "summary": "Order win", "source_refs": ["exchange://filing/1"]},
    )
    assert result["fii_accumulation"]["score"] == 83.75
    assert result["fii_accumulation"]["coverage_pct"] == 80.0
    assert result["market_confirmation"]["score"] == 70.59
    assert result["market_confirmation"]["coverage_pct"] == 85.0
    assert result["catalyst"]["score"] == 6
    assert result["catalyst"]["score"] not in (result["fii_accumulation"]["score"],)


def test_unverified_temporal_identity_cannot_score_direct_dimensions(config):
    fii = observations(
        {item["key"]: item["weight"] for item in config["primary_score"]["dimensions"]},
        {"verified_net_fpi_transactions": 0.9},
    )
    confirmation = observations(
        {item["key"]: item["weight"] for item in config["confirmation_score"]["dimensions"]}, {}
    )
    with pytest.raises(ValueError, match="cannot be scored"):
        score_stock(
            "ABC", fii, confirmation, config,
            identity_verified=True, direct_gate_passed=False,
        )


def test_missing_data_reduces_coverage_and_is_not_zero(config):
    fii = observations(
        {item["key"]: item["weight"] for item in config["primary_score"]["dimensions"]},
        {
            "verified_net_fpi_transactions": 0.7,
            "persistence": 0.7,
            "quarterly_fpi_ownership_change": 0.7,
        },
    )
    fii["independent_fpi_buyer_breadth"] = {
        "status": "not_applicable", "score": None,
        "reason": "no separately identifiable buyer groups in this validated feed",
    }
    confirmation = observations(
        {item["key"]: item["weight"] for item in config["confirmation_score"]["dimensions"]}, {}
    )
    result = score_stock(
        "ABC", fii, confirmation, config,
        identity_verified=True, direct_gate_passed=True,
    )
    assert result["fii_accumulation"]["score"] == 70.0
    assert result["fii_accumulation"]["coverage_pct"] == 88.89


def test_source_provenance_required_for_observed_zero(config):
    fii = observations(
        {item["key"]: item["weight"] for item in config["primary_score"]["dimensions"]}, {}
    )
    fii["quarterly_fpi_ownership_change"] = {
        "status": "observed", "score": 0.0, "source_refs": []
    }
    confirmation = observations(
        {item["key"]: item["weight"] for item in config["confirmation_score"]["dimensions"]}, {}
    )
    with pytest.raises(ValueError, match="requires source_refs"):
        score_stock(
            "ABC", fii, confirmation, config,
            identity_verified=True, direct_gate_passed=True,
        )


def test_unverified_universe_identity_blocks_rankability(config):
    fii = observations(
        {item["key"]: item["weight"] for item in config["primary_score"]["dimensions"]},
        {
            "verified_net_fpi_transactions": 1.0,
            "persistence": 1.0,
            "quarterly_fpi_ownership_change": 1.0,
        },
    )
    confirmation = observations(
        {item["key"]: item["weight"] for item in config["confirmation_score"]["dimensions"]}, {}
    )
    result = score_stock(
        "ABC", fii, confirmation, config,
        identity_verified=False, direct_gate_passed=True,
    )
    assert result["fii_accumulation"]["score"] == 100.0
    assert result["fii_accumulation"]["rankable"] is False
    assert result["fii_accumulation"]["status"] == "not_rankable_identity_unverified"


def test_catalyst_cannot_be_positive_without_source(config):
    fii = observations(
        {item["key"]: item["weight"] for item in config["primary_score"]["dimensions"]}, {}
    )
    confirmation = observations(
        {item["key"]: item["weight"] for item in config["confirmation_score"]["dimensions"]}, {}
    )
    with pytest.raises(ValueError, match="requires source references"):
        score_stock(
            "ABC", fii, confirmation, config,
            identity_verified=True, direct_gate_passed=False,
            catalyst_assessment={"score": 4, "summary": "Rumour", "source_refs": []},
        )


def test_config_rejects_weight_or_dimension_drift(config):
    broken = json.loads(json.dumps(config))
    broken["primary_score"]["dimensions"][0]["weight"] = 34
    with pytest.raises(ValueError, match="weights do not match"):
        validate_config(broken)
