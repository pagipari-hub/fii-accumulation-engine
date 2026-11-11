from datetime import date
from io import BytesIO
from urllib.parse import parse_qs, urlparse
import zipfile

import pytest

from fii_engine.nse_fo_historical_archive_probe import (
    REPORTS,
    _candidate_dates,
    _unwrap_download,
    build_archive_url,
)


def test_build_archive_url_targets_exact_report_and_date():
    url = build_archive_url(
        "participant_oi",
        date(2026, 10, 30),
        "derivatives",
        "equity-derivatives",
    )
    query = parse_qs(urlparse(url).query)
    assert query["date"] == ["30-Oct-2026"]
    assert query["type"] == ["derivatives"]
    assert query["mode"] == ["single"]
    assert query["archives"][0].find(REPORTS["participant_oi"]["name"]) >= 0


def test_build_archive_url_rejects_unknown_dataset():
    with pytest.raises(ValueError, match="unsupported"):
        build_archive_url("unknown", date(2026, 10, 30), "derivatives", "fo")


def test_unwrap_download_preserves_plain_payload():
    payload = b"Client Type,Future Index Long"
    assert _unwrap_download(payload, "participant_oi") == (payload, None)


def test_unwrap_download_extracts_single_member_zip():
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("participant.csv", "Client Type,Future Index Long")
    extracted, member = _unwrap_download(buffer.getvalue(), "participant_oi")
    assert member == "participant.csv"
    assert extracted == b"Client Type,Future Index Long"


def test_candidate_dates_skips_weekends():
    dates = list(_candidate_dates(date(2026, 11, 9), 3, 10))
    assert dates == [
        date(2026, 11, 9),
        date(2026, 11, 6),
        date(2026, 11, 5),
        date(2026, 11, 4),
        date(2026, 11, 3),
        date(2026, 11, 2),
    ]
