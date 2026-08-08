import json

import click
import pytest

from stac_dupes.cli import parse_query


def test_parse_inline_query() -> None:
    query = {"op": "=", "args": [1, 1]}

    assert parse_query(json.dumps(query)) == query


def test_parse_query_file(tmp_path) -> None:
    query_file = tmp_path / "filter.json"
    query_file.write_text('{"op": "and", "args": []}', encoding="utf-8")

    assert parse_query(f"@{query_file}") == {"op": "and", "args": []}


def test_parse_query_rejects_array() -> None:
    with pytest.raises(click.BadParameter, match="must be an object"):
        parse_query("[]")
