"""Click command-line interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from stac_dupes import config, crawler, db


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
    help="CQL2 JSON text or @path/to/filter.json for a new run.",
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
        if url is None or query is None:
            raise click.UsageError("--url and --query are required for a new run")
        cql2_filter = parse_query(query)
    else:
        if url is not None or query is not None or collections:
            raise click.UsageError(
                "--url, --query, and --collection cannot be used with --run-id"
            )
        cql2_filter = None

    _run_crawl(
        database_url=database_url,
        catalog_url=url,
        cql2_filter=cql2_filter,
        collections=list(collections),
        page_size=page_size,
        run_id=run_id,
        re_crawl=re_crawl,
    )


@main.command("interactive")
@database_option
def interactive(database_url: str) -> None:
    """Prompt for crawl parameters and start a new run."""
    url = click.prompt("STAC API URL", type=str)
    query = click.prompt("CQL2 JSON or @file", type=str)
    collection_text = click.prompt(
        "Collection IDs (comma-separated, blank for all)", default="", show_default=False
    )
    page_size = click.prompt("Page size", default=100, type=click.IntRange(min=1))
    collections = [value.strip() for value in collection_text.split(",") if value.strip()]

    _run_crawl(
        database_url=database_url,
        catalog_url=url,
        cql2_filter=parse_query(query),
        collections=collections,
        page_size=page_size,
        run_id=None,
        re_crawl=False,
    )


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


def _run_crawl(
    *,
    database_url: str,
    catalog_url: str | None,
    cql2_filter: dict[str, Any] | None,
    collections: list[str],
    page_size: int,
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
                run_id=run_id,
                re_crawl=re_crawl,
            )
    except Exception as error:
        raise click.ClickException(str(error)) from error
    click.echo(
        f"Run {result.run_id} completed: "
        f"{result.seen} item(s) seen, {result.ingested} item(s) ingested."
    )


if __name__ == "__main__":
    main()
