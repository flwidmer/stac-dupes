import pytest
import requests

from stac_dupes import catalog


def test_suggest_partitions_recursively_splits_oversized_bucket() -> None:
    properties = {
        "product:type": {"enum": ["S1", "S2", "S3"]},
        "sat:orbit_state": {"enum": ["ASCENDING", "DESCENDING"]},
    }
    counts = {
        "product:type=S1": 101,
        "product:type=S2": 70,
        "product:type=S3": 60,
        "product:type=S1, sat:orbit_state=ASCENDING": 51,
        "product:type=S1, sat:orbit_state=DESCENDING": 50,
    }

    partitions = catalog.suggest_partitions(
        matched=231,
        result_limit=100,
        properties=properties,
        count=lambda cql2_filter: counts[catalog.describe_filter(cql2_filter)],
    )

    assert [catalog.describe_filter(value.cql2_filter) for value in partitions] == [
        "product:type=S1, sat:orbit_state=ASCENDING",
        "product:type=S1, sat:orbit_state=DESCENDING",
        "product:type=S2",
        "product:type=S3",
    ]
    assert [value.count for value in partitions] == [51, 50, 70, 60]


def test_suggest_partitions_rejects_incomplete_enum_coverage() -> None:
    properties = {"product:type": {"enum": ["S1", "S2"]}}

    with pytest.raises(catalog.PartitionPlanningError, match="no advertised enum"):
        catalog.suggest_partitions(
            matched=250,
            result_limit=100,
            properties=properties,
            count=lambda cql2_filter: 50,
        )


def test_suggest_partitions_does_not_repeat_base_filter_property() -> None:
    base_filter = {"op": "=", "args": [{"property": "product:type"}, "S1"]}
    properties = {
        "product:type": {"enum": ["S1", "S2"]},
        "sat:orbit_state": {"enum": ["ASCENDING", "DESCENDING"]},
    }
    counts = {
        "product:type=S1, sat:orbit_state=ASCENDING": 51,
        "product:type=S1, sat:orbit_state=DESCENDING": 50,
    }

    partitions = catalog.suggest_partitions(
        matched=101,
        result_limit=100,
        properties=properties,
        count=lambda cql2_filter: counts[catalog.describe_filter(cql2_filter)],
        base_filter=base_filter,
    )

    assert [catalog.describe_filter(value.cql2_filter) for value in partitions] == [
        "product:type=S1, sat:orbit_state=ASCENDING",
        "product:type=S1, sat:orbit_state=DESCENDING",
    ]


def test_enum_queryables_excludes_single_and_large_enums() -> None:
    properties = {
        "single": {"enum": ["only"]},
        "useful": {"enum": ["one", "two"]},
        "large": {"enum": list(range(catalog.MAX_PARTITION_VALUES + 1))},
        "free-text": {"type": "string"},
    }

    assert catalog.enum_queryables(properties) == {"useful": ["one", "two"]}


def test_collection_ids_follows_pagination(monkeypatch) -> None:
    pages = [
        {
            "collections": [{"id": "second"}],
            "links": [{"rel": "next", "href": "https://example.test/page/2"}],
        },
        {"collections": [{"id": "first"}], "links": []},
    ]
    calls = []

    def get(url: str, **kwargs) -> requests.Response:
        calls.append((url, kwargs.get("params")))
        response = requests.Response()
        response.status_code = 200
        response.json = lambda: pages.pop(0)
        return response

    monkeypatch.setattr(catalog.requests, "get", get)

    assert catalog.collection_ids("https://example.test") == ["first", "second"]
    assert calls == [
        ("https://example.test/collections", {"limit": 100}),
        ("https://example.test/page/2", None),
    ]
