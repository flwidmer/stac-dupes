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


def test_interactive_all_uses_collection_mode_when_result_fits(monkeypatch) -> None:
    arguments = {}
    monkeypatch.setattr(cli.catalog, "collection_ids", lambda url: ["example"])
    monkeypatch.setattr(
        cli.catalog, "collection_window", lambda url, collection: (90, 100)
    )
    monkeypatch.setattr(cli, "_run_crawl", lambda **kwargs: arguments.update(kwargs))

    result = CliRunner().invoke(
        cli.main,
        ["interactive"],
        input="https://example.test\nexample\nall\n100\n",
    )

    assert result.exit_code == 0
    assert arguments["crawl_mode"] == cli.crawler.COLLECTION_ITEMS_MODE
    assert arguments["collections"] == ["example"]


def test_interactive_builds_filter_from_queryable_enum(monkeypatch) -> None:
    arguments = {}
    monkeypatch.setattr(cli.catalog, "collection_ids", lambda url: ["example"])
    monkeypatch.setattr(
        cli.catalog,
        "queryables",
        lambda url, collection: {
            "product:type": {
                "title": "Product type",
                "enum": ["S1", "S2"],
            }
        },
    )
    monkeypatch.setattr(cli.catalog, "count_search", lambda *args: 50)
    monkeypatch.setattr(
        cli.catalog, "collection_window", lambda url, collection: (200, 100)
    )
    monkeypatch.setattr(cli, "_run_crawl", lambda **kwargs: arguments.update(kwargs))

    result = CliRunner().invoke(
        cli.main,
        ["interactive"],
        input="https://example.test\nexample\nfilters\n100\nproduct:type\nS1\n",
    )

    assert result.exit_code == 0
    assert arguments["cql2_filter"] == {
        "op": "=",
        "args": [{"property": "product:type"}, "S1"],
    }
    assert arguments["crawl_mode"] == cli.crawler.SEARCH_MODE


def test_interactive_proposes_partitions_for_oversized_collection(monkeypatch) -> None:
    arguments = {}
    monkeypatch.setattr(cli.catalog, "collection_ids", lambda url: ["example"])
    monkeypatch.setattr(
        cli.catalog, "collection_window", lambda url, collection: (250, 100)
    )
    monkeypatch.setattr(
        cli,
        "_run_partition_plan",
        lambda **kwargs: arguments.update(kwargs),
    )

    result = CliRunner().invoke(
        cli.main,
        ["interactive"],
        input="https://example.test\nexample\nall\n100\n",
    )

    assert result.exit_code == 0
    assert arguments["matched"] == 250
    assert arguments["result_limit"] == 100
    assert arguments["base_filter"] is None


def test_collection_limit_error_includes_suggested_partitions(monkeypatch) -> None:
    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

    def fail_crawl(*args, **kwargs):
        limit_error = cli.crawler.CatalogResultLimitError(250, 100)
        raise cli.crawler.CrawlError(12, 0, 0, limit_error) from limit_error

    partition_filter = {
        "op": "=",
        "args": [{"property": "product:type"}, "S1"],
    }
    monkeypatch.setattr(cli.db, "connect", lambda url: Connection())
    monkeypatch.setattr(cli.crawler, "crawl", fail_crawl)
    monkeypatch.setattr(
        cli,
        "_suggested_partitions",
        lambda *args: [cli.catalog.QueryPartition(partition_filter, 90)],
    )

    with pytest.raises(click.ClickException, match="Suggested filtered runs") as raised:
        cli._run_crawl(
            database_url="postgresql://example",
            catalog_url="https://example.test",
            cql2_filter={},
            collections=["example"],
            page_size=100,
            crawl_mode=cli.crawler.COLLECTION_ITEMS_MODE,
            run_id=None,
            re_crawl=False,
        )

    message = str(raised.value)
    assert "product:type=S1: 90 item(s)" in message
    compact_filter = json.dumps(partition_filter, separators=(",", ":"))
    assert f"--query '{compact_filter}'" in message


def test_format_partition_includes_direct_cql2_query() -> None:
    partition = cli.catalog.QueryPartition(
        {
            "op": "and",
            "args": [
                {"op": "=", "args": [{"property": "product:type"}, "S1"]},
                {
                    "op": "=",
                    "args": [{"property": "sat:orbit_state"}, "ASCENDING"],
                },
            ],
        },
        50_368,
    )

    output = cli._format_partition(partition)

    assert "product:type=S1, sat:orbit_state=ASCENDING: 50,368 item(s)" in output
    compact_filter = json.dumps(partition.cql2_filter, separators=(",", ":"))
    assert output.endswith(f"--query '{compact_filter}'")
