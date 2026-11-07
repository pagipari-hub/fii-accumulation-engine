from datetime import date, datetime, timezone

import pytest

from fii_engine.models import (
    FnoStatus,
    SourceRecord,
    UniverseConstituent,
    validate_universe,
)


def member(start: date, end: date | None = None) -> UniverseConstituent:
    return UniverseConstituent(
        symbol="ABC",
        company_name="ABC Limited",
        effective_from=start,
        effective_to=end,
        fno_status=FnoStatus.UNKNOWN,
    )


def test_cash_only_or_unknown_fno_status_is_not_assumed_ineligible() -> None:
    record = member(date(2026, 1, 1))
    assert record.fno_status is FnoStatus.UNKNOWN


def test_non_overlapping_membership_periods_are_valid() -> None:
    validate_universe([
        member(date(2025, 1, 1), date(2025, 6, 30)),
        member(date(2025, 7, 1)),
    ])


def test_overlapping_membership_periods_are_rejected() -> None:
    with pytest.raises(ValueError, match="overlapping"):
        validate_universe([
            member(date(2025, 1, 1), date(2025, 8, 1)),
            member(date(2025, 8, 1)),
        ])


def test_source_record_requires_aware_retrieval_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        SourceRecord(
            source_id="test",
            publisher="test",
            dataset="test",
            source_uri=None,
            published_at=None,
            retrieved_at=datetime(2026, 1, 1),
            content_sha256="a" * 64,
        )


def test_source_record_accepts_valid_sha256_and_timestamp() -> None:
    record = SourceRecord(
        source_id="test",
        publisher="test",
        dataset="test",
        source_uri=None,
        published_at=None,
        retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        content_sha256="a" * 64,
    )
    assert record.is_provisional is False
