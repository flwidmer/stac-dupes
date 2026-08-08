"""STAC catalog discovery and result-set partition planning."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests

PREFERRED_PARTITION_PROPERTIES = (
    "product:type",
    "sat:orbit_state",
    "version",
    "eofeos:major_cycle_id",
    "eofeos:repeat_cycle_id",
)
MAX_PARTITION_VALUES = 20
START_RECORD_LIMIT = re.compile(
    r"startRecord\s+can(?:not| not)\s+exceed\s+(\d+)", re.IGNORECASE
)


class PartitionPlanningError(ValueError):
    """The advertised queryables cannot safely partition a result set."""


@dataclass(frozen=True)
class QueryPartition:
    """One bounded CQL2 result set."""

    cql2_filter: dict[str, Any]
    count: int


def collection_ids(catalog_url: str) -> list[str]:
    """Return collection IDs advertised by a STAC catalog."""
    url: str | None = f"{catalog_url.rstrip('/')}/collections"
    params: dict[str, int] | None = {"limit": 100}
    identifiers = []
    visited = set()
    while url is not None and url not in visited:
        visited.add(url)
        response = requests.get(url, params=params, timeout=60)
        response.raise_for_status()
        page = response.json()
        identifiers.extend(
            collection["id"]
            for collection in page.get("collections", [])
            if collection.get("id")
        )
        next_link = next(
            (link for link in page.get("links", []) if link.get("rel") == "next"),
            None,
        )
        url = next_link["href"] if next_link is not None else None
        params = None
    return sorted(set(identifiers))


def queryables(catalog_url: str, collection: str) -> dict[str, dict[str, Any]]:
    """Return queryable property definitions for one collection."""
    encoded = quote(collection, safe="")
    response = requests.get(
        f"{catalog_url.rstrip('/')}/collections/{encoded}/queryables",
        timeout=60,
    )
    response.raise_for_status()
    properties = response.json().get("properties", {})
    return properties if isinstance(properties, dict) else {}


def count_search(
    catalog_url: str,
    collection: str,
    cql2_filter: dict[str, Any],
) -> int:
    """Count a CQL2 search while retrieving only one Item."""
    response = requests.post(
        f"{catalog_url.rstrip('/')}/search",
        json={
            "collections": [collection],
            "filter-lang": "cql2-json",
            "filter": cql2_filter,
            "limit": 1,
        },
        timeout=60,
    )
    response.raise_for_status()
    return int(response.json()["numberMatched"])


def collection_window(catalog_url: str, collection: str) -> tuple[int, int | None]:
    """Return collection size and any result-window limit reported by the catalog."""
    encoded = quote(collection, safe="")
    response = requests.get(
        f"{catalog_url.rstrip('/')}/collections/{encoded}/items",
        params={"limit": 1},
        timeout=60,
    )
    response.raise_for_status()
    page = response.json()
    matched = int(page["numberMatched"])
    last_link = next(
        (link for link in page.get("links", []) if link.get("rel") == "last"),
        None,
    )
    if last_link is None:
        return matched, None

    last_response = requests.get(last_link["href"], timeout=60)
    return matched, _start_record_limit(last_response)


def enum_queryables(
    properties: dict[str, dict[str, Any]],
) -> dict[str, list[Any]]:
    """Select finite queryables suitable for prompts and partitioning."""
    return {
        name: definition["enum"]
        for name, definition in properties.items()
        if isinstance(definition, dict)
        and isinstance(definition.get("enum"), list)
        and 1 < len(definition["enum"]) <= MAX_PARTITION_VALUES
    }


def suggest_partitions(
    *,
    matched: int,
    result_limit: int,
    properties: dict[str, dict[str, Any]],
    count: Callable[[dict[str, Any]], int],
    base_filter: dict[str, Any] | None = None,
) -> list[QueryPartition]:
    """Recursively split a result set using complete advertised enum values."""
    if matched <= result_limit:
        if base_filter is None:
            raise PartitionPlanningError("an unfiltered result set needs no partition")
        return [QueryPartition(base_filter, matched)]

    enums = enum_queryables(properties)
    used_properties = _filter_properties(base_filter)
    candidates = sorted(
        (name for name in enums if name not in used_properties),
        key=lambda name: (
            PREFERRED_PARTITION_PROPERTIES.index(name)
            if name in PREFERRED_PARTITION_PROPERTIES
            else len(PREFERRED_PARTITION_PROPERTIES),
            len(enums[name]),
            name,
        ),
    )
    partitions = _partition_result(
        base_filter,
        matched,
        result_limit,
        candidates,
        enums,
        count,
    )
    if partitions is None:
        raise PartitionPlanningError(
            "no advertised enum queryable safely partitions the result set"
        )
    return partitions


def describe_filter(cql2_filter: dict[str, Any]) -> str:
    """Describe equality filters used in a generated partition."""
    if cql2_filter.get("op") == "and":
        return ", ".join(describe_filter(arg) for arg in cql2_filter["args"])
    args = cql2_filter.get("args", [])
    if cql2_filter.get("op") == "=" and len(args) == 2 and isinstance(args[0], dict):
        return f"{args[0].get('property')}={args[1]}"
    return str(cql2_filter)


def _partition_result(
    base_filter: dict[str, Any] | None,
    matched: int,
    result_limit: int,
    candidates: list[str],
    enums: dict[str, list[Any]],
    count: Callable[[dict[str, Any]], int],
) -> list[QueryPartition] | None:
    for index, property_name in enumerate(candidates):
        children = []
        for value in enums[property_name]:
            child_filter = _combine_filter(base_filter, property_name, value)
            child_count = count(child_filter)
            if child_count:
                children.append((child_filter, child_count))

        tolerance = max(10, matched // 100)
        if (
            not children
            or abs(sum(value for _, value in children) - matched) > tolerance
        ):
            continue

        partitions: list[QueryPartition] = []
        remaining = candidates[index + 1 :]
        for child_filter, child_count in children:
            if child_count <= result_limit:
                partitions.append(QueryPartition(child_filter, child_count))
                continue
            nested = _partition_result(
                child_filter,
                child_count,
                result_limit,
                remaining,
                enums,
                count,
            )
            if nested is None:
                break
            partitions.extend(nested)
        else:
            return partitions
    return None


def _combine_filter(
    base_filter: dict[str, Any] | None,
    property_name: str,
    value: Any,
) -> dict[str, Any]:
    equality = {"op": "=", "args": [{"property": property_name}, value]}
    if base_filter is None:
        return equality
    return {"op": "and", "args": [base_filter, equality]}


def _start_record_limit(response: requests.Response) -> int | None:
    try:
        payload = response.json()
    except requests.JSONDecodeError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    match = START_RECORD_LIMIT.search(message) if isinstance(message, str) else None
    return int(match.group(1)) if match else None


def _filter_properties(cql2_filter: dict[str, Any] | None) -> set[str]:
    if cql2_filter is None:
        return set()
    properties = set()
    for argument in cql2_filter.get("args", []):
        if isinstance(argument, dict) and isinstance(argument.get("property"), str):
            properties.add(argument["property"])
        elif isinstance(argument, dict):
            properties.update(_filter_properties(argument))
    return properties
