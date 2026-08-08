from datetime import datetime, timezone

from stac_dupes.db import sensing_times


def test_sensing_times_uses_datetime_as_instant() -> None:
    expected = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    assert sensing_times({"datetime": "2024-01-02T03:04:05Z"}) == (
        expected,
        expected,
    )


def test_sensing_times_uses_interval() -> None:
    start, end = sensing_times(
        {
            "datetime": None,
            "start_datetime": "2024-01-02T03:04:05+00:00",
            "end_datetime": "2024-01-03T03:04:05+00:00",
        }
    )

    assert start is not None
    assert end is not None
    assert start < end
