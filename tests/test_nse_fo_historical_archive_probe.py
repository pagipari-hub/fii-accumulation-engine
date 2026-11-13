from datetime import date
from io import BytesIO
from urllib.parse import urlparse
import zipfile

import pytest

from fii_engine.nse_fo_historical_archive_probe import (
    REPORTS,
    _candidate_dates,
    _unwrap_download,
    build_archive_url,
)


@pytest.mark.parametrize(
    ("dataset", "expected_path"),
    [
        (
            "participant_oi",
            "/content/nsccl/fao_participant_oi_30102026.csv",
        ),
        (
            "participant_volume",
            "/content/nsccl/fao_participant_vol_30102026.csv",
        ),
        (
            "fii_derivatives_statistics",
            "/content/fo/fii_stats_30-Oct-2026.xls",
        ),
    ],
)
def test_build_archive_url_uses_documented_historical_archive_path(
    dataset, expected_path
):
    url = build_archive_url(dataset, date(2026, 10, 30))
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "nsearchives.nseindia.com"
    assert parsed.path == expected_path
    assert dataset in REPORTS


def test_build_archive_url_rejects_unknown_dataset():
    with pytest.raises(ValueError, match="unsupported"):
        build_archive_url("unknown", date(2026, 10, 30))


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
    dates = list(_candidate_dates(date(2026, 11, 9), 10))
    assert dates == [
        date(2026, 11, 9),
        date(2026, 11, 6),
        date(2026, 11, 5),
        date(2026, 11, 4),
        date(2026, 11, 3),
        date(2026, 11, 2),
    ]
