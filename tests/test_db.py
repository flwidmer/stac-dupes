from datetime import UTC, datetime

from stac_dupes.db import processing_baseline, product_type, sensing_times


def test_sensing_times_uses_datetime_as_instant() -> None:
    expected = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)

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


def test_processing_baseline_preserves_text_version() -> None:
    assert processing_baseline({"version": "02"}) == "02"


def test_processing_baseline_allows_missing_version() -> None:
    assert processing_baseline({}) is None


def test_product_type_uses_namespaced_property() -> None:
    assert product_type({"product:type": "FP_GN__L2A"}) == "FP_GN__L2A"


def test_product_type_allows_missing_property() -> None:
    assert product_type({}) is None
