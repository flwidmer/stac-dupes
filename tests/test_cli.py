import json

import click
import pytest
from click.testing import CliRunner

from stac_dupes import cli
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


def test_crawl_accepts_collection_without_query(monkeypatch) -> None:
    arguments = {}
    monkeypatch.setattr(cli, "_run_crawl", lambda **kwargs: arguments.update(kwargs))

    result = CliRunner().invoke(
        cli.main,
        ["crawl", "--url", "https://example.test", "--collection", "example"],
    )

    assert result.exit_code == 0
    assert arguments["cql2_filter"] == {}
    assert arguments["collections"] == ["example"]
    assert arguments["crawl_mode"] == cli.crawler.COLLECTION_ITEMS_MODE


def test_collection_crawl_rejects_multiple_collections(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_run_crawl", lambda **kwargs: None)

    result = CliRunner().invoke(
        cli.main,
        [
            "crawl",
            "--url",
            "https://example.test",
            "--collection",
            "first",
            "--collection",
            "second",
        ],
    )

    assert result.exit_code == 2
    assert "exactly one --collection" in result.output


def test_crawl_with_query_uses_search_mode(monkeypatch) -> None:
    arguments = {}
    monkeypatch.setattr(cli, "_run_crawl", lambda **kwargs: arguments.update(kwargs))

    result = CliRunner().invoke(
        cli.main,
        ["crawl", "--url", "https://example.test", "--query", "{}"],
    )

    assert result.exit_code == 0
    assert arguments["crawl_mode"] == cli.crawler.SEARCH_MODE
