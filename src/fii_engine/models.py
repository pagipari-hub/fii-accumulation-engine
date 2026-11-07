"""Canonical, source-agnostic domain models.

All dates are exchange-effective dates, not scrape or ingestion timestamps.
Missing F&O eligibility is represented as UNKNOWN, never as not eligible.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import StrEnum


class FnoStatus(StrEnum):
    ELIGIBLE = "eligible"
    NOT_ELIGIBLE = "not_eligible"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class UniverseConstituent:
    symbol: str
    company_name: str
    effective_from: date
    effective_to: date | None = None
    series: str | None = None
    isin: str | None = None
    sector: str | None = None
    industry: str | None = None
    fno_status: FnoStatus = FnoStatus.UNKNOWN
    exchange: str = "NSE"
    source_id: str | None = None

    def __post_init__(self) -> None:
        if not self.symbol or self.symbol.strip() != self.symbol:
            raise ValueError("symbol must be non-empty and have no surrounding whitespace")
        if not self.company_name.strip():
            raise ValueError("company_name must be non-empty")
        if self.exchange != "NSE":
            raise ValueError("this universe currently supports NSE listings only")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to cannot precede effective_from")


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """Provenance for one ingested source artifact or dataset."""

    source_id: str
    publisher: str
    dataset: str
    source_uri: str | None
    published_at: datetime | None
    retrieved_at: datetime
    content_sha256: str
    is_provisional: bool = False

    def __post_init__(self) -> None:
        if not self.source_id or not self.publisher or not self.dataset:
            raise ValueError("source_id, publisher and dataset are required")
        if len(self.content_sha256) != 64:
            raise ValueError("content_sha256 must be a 64-character SHA-256 hex digest")
        try:
            int(self.content_sha256, 16)
        except ValueError as exc:
            raise ValueError("content_sha256 must be hexadecimal") from exc
        if self.retrieved_at.tzinfo is None:
            raise ValueError("retrieved_at must be timezone-aware")
        if self.published_at is not None and self.published_at.tzinfo is None:
            raise ValueError("published_at must be timezone-aware")


def utc_now() -> datetime:
    """Return an aware UTC timestamp for ingestion metadata."""
    return datetime.now(timezone.utc)


def validate_universe(records: list[UniverseConstituent]) -> None:
    """Reject duplicate/overlapping symbol membership intervals.

    Adjacent intervals are allowed when the previous interval ends the day
    before the next interval starts. Open-ended intervals overlap all later
    intervals for the same symbol.
    """
    by_symbol: dict[str, list[UniverseConstituent]] = {}
    for record in records:
        by_symbol.setdefault(record.symbol, []).append(record)

    for symbol, items in by_symbol.items():
        ordered = sorted(items, key=lambda item: item.effective_from)
        for previous, current in zip(ordered, ordered[1:]):
            if previous.effective_to is None or current.effective_from <= previous.effective_to:
                raise ValueError(f"overlapping membership intervals for {symbol}")
