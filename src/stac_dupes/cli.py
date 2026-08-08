"""Click command-line interface."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

import click
import requests

from stac_dupes import catalog, config, crawler, db


@click.group()
@click.version_option()
def main() -> None:
    """Ingest STAC Items into PostGIS for duplicate analysis."""


def database_option(function: Any) -> Any:
    """Attach the shared database URL option to a command."""
    return click.option(
        "--database-url",
        envvar="DATABASE_URL",
        default=config.DEFAULT_DATABASE_URL,
        show_default=True,
        help="PostgreSQL connection URL.",
    )(function)


@main.command("init-db")
@database_option
def init_db(database_url: str) -> None:
    """Create or update the database schema."""
    try:
        with db.connect(database_url) as connection:
            applied = db.apply_migrations(connection)
    except Exception as error:
        raise click.ClickException(str(error)) from error

    if applied:
        click.echo(f"Applied {len(applied)} migration(s): {', '.join(applied)}")
    else:
        click.echo("Database schema is up to date.")


@main.command("crawl")
@click.option("--url", help="STAC API root URL for a new run.")
@click.option(
    "--query",
    help="Optional CQL2 JSON text or @path/to/filter.json for a new run.",
)
@click.option(
    "--collection",
    "collections",
    multiple=True,
    help="Collection ID; repeat to search multiple collections.",
)
@click.option(
    "--page-size",
    type=click.IntRange(min=1),
    default=100,
    show_default=True,
)
@click.option("--run-id", type=click.IntRange(min=1), help="Resume an existing run.")
@click.option(
    "--re-crawl",
    is_flag=True,
    help="Restart the saved run's query instead of resuming its checkpoint.",
)
@database_option
def crawl_command(
    url: str | None,
    query: str | None,
    collections: tuple[str, ...],
    page_size: int,
    run_id: int | None,
    re_crawl: bool,
    database_url: str,
) -> None:
    """Crawl a CQL2 search or resume a saved run."""
    if re_crawl and run_id is None:
        raise click.UsageError("--re-crawl requires --run-id")
    if run_id is None:
        if url is None:
            raise click.UsageError("--url is required for a new run")
        if query is None and not collections:
            raise click.UsageError("--query or --collection is required for a new run")
        if query is None and len(collections) != 1:
            raise click.UsageError(
                "exactly one --collection is required without --query"
            )
        cql2_filter = parse_query(query) if query is not None else {}
        crawl_mode = (
            crawler.SEARCH_MODE if query is not None else crawler.COLLECTION_ITEMS_MODE
        )
    else:
        if url is not None or query is not None or collections:
            raise click.UsageError(
                "--url, --query, and --collection cannot be used with --run-id"
            )
        cql2_filter = None
        crawl_mode = None

    _run_crawl(
        database_url=database_url,
        catalog_url=url,
        cql2_filter=cql2_filter,
        collections=list(collections),
        page_size=page_size,
        crawl_mode=crawl_mode,
        run_id=run_id,
        re_crawl=re_crawl,
    )


@main.command("interactive")
@database_option
def interactive(database_url: str) -> None:
    """Discover queryables and interactively start bounded crawl runs."""
    url = click.prompt("STAC API URL", type=str)
    try:
        collections = catalog.collection_ids(url)
    except (requests.RequestException, KeyError, ValueError) as error:
        raise click.ClickException(f"Could not load collections: {error}") from error
    if not collections:
        raise click.ClickException("The catalog did not advertise any collections")

    click.echo(f"Discovered {len(collections):,} collection(s).")
    collection = _prompt_collection(collections)
    mode = click.prompt(
        "Crawl mode",
        type=click.Choice(["all", "filters", "json"]),
        default="all",
    )
    page_size = click.prompt("Page size", default=100, type=click.IntRange(min=1))

    try:
        if mode == "all":
            matched, result_limit = catalog.collection_window(url, collection)
            click.echo(f"Collection contains {matched:,} item(s).")
            if result_limit is None or matched <= result_limit:
                _run_crawl(
                    database_url=database_url,
                    catalog_url=url,
                    cql2_filter={},
                    collections=[collection],
                    page_size=page_size,
                    crawl_mode=crawler.COLLECTION_ITEMS_MODE,
                    run_id=None,
                    re_crawl=False,
                )
                return
            _run_partition_plan(
                database_url=database_url,
                catalog_url=url,
                collection=collection,
                base_filter=None,
                matched=matched,
                result_limit=result_limit,
                page_size=page_size,
            )
            return

        properties = catalog.queryables(url, collection)
        cql2_filter = (
            _prompt_queryable_filter(properties)
            if mode == "filters"
            else parse_query(click.prompt("CQL2 JSON or @file", type=str))
        )
        matched = catalog.count_search(url, collection, cql2_filter)
        _, result_limit = catalog.collection_window(url, collection)
        click.echo(f"Filter matches {matched:,} item(s).")
        if result_limit is not None and matched > result_limit:
            _run_partition_plan(
                database_url=database_url,
                catalog_url=url,
                collection=collection,
                base_filter=cql2_filter,
                matched=matched,
                result_limit=result_limit,
                page_size=page_size,
                properties=properties,
            )
            return
        _run_crawl(
            database_url=database_url,
            catalog_url=url,
            cql2_filter=cql2_filter,
            collections=[collection],
            page_size=page_size,
            crawl_mode=crawler.SEARCH_MODE,
            run_id=None,
            re_crawl=False,
        )
    except (
        requests.RequestException,
        KeyError,
        ValueError,
    ) as error:
        raise click.ClickException(str(error)) from error


def parse_query(value: str) -> dict[str, Any]:
    """Parse inline CQL2 JSON or an @-prefixed JSON file."""
    if value.startswith("@"):
        path = Path(value[1:])
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as error:
            raise click.BadParameter(str(error), param_hint="--query") from error
    else:
        raw = value

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise click.BadParameter(
            f"invalid JSON: {error.msg} at line {error.lineno}, column {error.colno}",
            param_hint="--query",
        ) from error
    if not isinstance(parsed, dict):
        raise click.BadParameter("CQL2 JSON must be an object", param_hint="--query")
    return parsed


def _prompt_collection(collections: list[str]) -> str:
    search = click.prompt("Collection ID or search text", type=str)
    if search in collections:
        return search
    matches = [value for value in collections if search.casefold() in value.casefold()]
    if not matches:
        raise click.ClickException(f"No collection matches {search!r}")
    if len(matches) == 1:
        click.echo(f"Using collection {matches[0]}")
        return matches[0]
    return click.prompt(
        "Collection",
        type=click.Choice(matches, case_sensitive=True),
    )


def _prompt_queryable_filter(
    properties: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    enums = catalog.enum_queryables(properties)
    if not enums:
        raise click.ClickException(
            "The collection does not advertise finite enum queryables"
        )

    filters = []
    remaining = list(enums)
    while remaining:
        property_name = click.prompt(
            "Queryable",
            type=click.Choice(remaining, case_sensitive=True),
        )
        values = enums[property_name]
        value_labels = {str(value): value for value in values}
        selected = click.prompt(
            properties[property_name].get("title", property_name),
            type=click.Choice(list(value_labels), case_sensitive=True),
        )
        filters.append(
            {
                "op": "=",
                "args": [{"property": property_name}, value_labels[selected]],
            }
        )
        remaining.remove(property_name)
        if not remaining or not click.confirm("Add another filter?", default=False):
            break
    return filters[0] if len(filters) == 1 else {"op": "and", "args": filters}


def _run_partition_plan(
    *,
    database_url: str,
    catalog_url: str,
    collection: str,
    base_filter: dict[str, Any] | None,
    matched: int,
    result_limit: int,
    page_size: int,
    properties: dict[str, dict[str, Any]] | None = None,
) -> None:
    partitions = _suggested_partitions(
        catalog_url,
        collection,
        matched,
        result_limit,
        base_filter,
        properties,
    )
    click.echo(
        f"The catalog limits result sets to {result_limit:,} items. "
        f"Proposed {len(partitions)} partition(s):"
    )
    for partition in partitions:
        click.echo(_format_partition(partition))
    if not click.confirm("Start all proposed crawl runs?", default=False):
        return
    for partition in partitions:
        _run_crawl(
            database_url=database_url,
            catalog_url=catalog_url,
            cql2_filter=partition.cql2_filter,
            collections=[collection],
            page_size=page_size,
            crawl_mode=crawler.SEARCH_MODE,
            run_id=None,
            re_crawl=False,
        )


def _suggested_partitions(
    catalog_url: str,
    collection: str,
    matched: int,
    result_limit: int,
    base_filter: dict[str, Any] | None = None,
    properties: dict[str, dict[str, Any]] | None = None,
) -> list[catalog.QueryPartition]:
    properties = properties or catalog.queryables(catalog_url, collection)
    return catalog.suggest_partitions(
        matched=matched,
        result_limit=result_limit,
        properties=properties,
        count=lambda cql2_filter: catalog.count_search(
            catalog_url, collection, cql2_filter
        ),
        base_filter=base_filter,
    )


def _run_crawl(
    *,
    database_url: str,
    catalog_url: str | None,
    cql2_filter: dict[str, Any] | None,
    collections: list[str],
    page_size: int,
    crawl_mode: str | None,
    run_id: int | None,
    re_crawl: bool,
) -> None:
    try:
        with db.connect(database_url) as connection:
            result = crawler.crawl(
                connection,
                catalog_url=catalog_url,
                cql2_filter=cql2_filter,
                collections=collections,
                page_size=page_size,
                crawl_mode=crawl_mode or crawler.SEARCH_MODE,
                run_id=run_id,
                re_crawl=re_crawl,
            )
    except Exception as error:
        message = str(error)
        limit_error = _find_catalog_limit_error(error)
        if (
            limit_error is not None
            and crawl_mode == crawler.COLLECTION_ITEMS_MODE
            and catalog_url is not None
            and len(collections) == 1
        ):
            try:
                partitions = _suggested_partitions(
                    catalog_url,
                    collections[0],
                    limit_error.matched,
                    limit_error.limit,
                )
                suggestions = "\n".join(
                    _format_partition(partition) for partition in partitions
                )
                message = f"{message}\nSuggested filtered runs:\n{suggestions}"
            except (requests.RequestException, KeyError, ValueError):
                pass
        raise click.ClickException(message) from error
    click.echo(
        f"Run {result.run_id} completed: "
        f"{result.seen} item(s) seen, {result.ingested} item(s) ingested."
    )


def _find_catalog_limit_error(
    error: BaseException,
) -> crawler.CatalogResultLimitError | None:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, crawler.CatalogResultLimitError):
            return current
        current = current.__cause__
    return None


def _format_partition(partition: catalog.QueryPartition) -> str:
    cql2_json = json.dumps(partition.cql2_filter, separators=(",", ":"))
    return (
        f"  {catalog.describe_filter(partition.cql2_filter)}: "
        f"{partition.count:,} item(s)\n"
        f"    --query {shlex.quote(cql2_json)}"
    )


if __name__ == "__main__":
    main()
