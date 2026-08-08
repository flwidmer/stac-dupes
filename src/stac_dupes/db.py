"""Database migration and ingestion operations."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from datetime import datetime
from importlib import resources
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def connect(database_url: str) -> psycopg.Connection[dict[str, Any]]:
    """Open a dictionary-row PostgreSQL connection."""
    return psycopg.connect(database_url, row_factory=dict_row)


def apply_migrations(connection: psycopg.Connection[Any]) -> list[str]:
    """Apply all pending packaged SQL migrations in filename order."""
    newly_applied: list[str] = []
    with connection.transaction():
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        connection.execute("SELECT pg_advisory_xact_lock(hashtext('stac-dupes-migrations'))")
        applied = {
            row["version"]
            for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }
        migration_dir = resources.files("stac_dupes.migrations")
        migrations = sorted(
            entry
            for entry in migration_dir.iterdir()
            if entry.is_file() and MIGRATION_NAME.match(entry.name)
        )

        for migration in migrations:
            if migration.name in applied:
                continue
            connection.execute(migration.read_text(encoding="utf-8"))
            connection.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)",
                (migration.name,),
            )
            newly_applied.append(migration.name)
    return newly_applied


def create_run(
    connection: psycopg.Connection[Any],
    catalog_url: str,
    cql2_filter: dict[str, Any],
    collections: Sequence[str],
    page_size: int,
) -> dict[str, Any]:
    """Create and return a new ingestion run."""
    with connection.transaction():
        return connection.execute(
            """
            INSERT INTO ingest_runs (
                catalog_url, cql2_filter, collections, page_size, state
            )
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                catalog_url,
                Jsonb(cql2_filter),
                Jsonb(list(collections)),
                page_size,
                Jsonb({"seen": 0, "ingested": 0}),
            ),
        ).fetchone()


def get_run(
    connection: psycopg.Connection[Any], run_id: int
) -> dict[str, Any] | None:
    """Load an ingestion run by primary key."""
    with connection.transaction():
        return connection.execute(
            "SELECT * FROM ingest_runs WHERE id = %s", (run_id,)
        ).fetchone()


def reset_run(connection: psycopg.Connection[Any], run_id: int) -> dict[str, Any]:
    """Reset a run so its original query is crawled from the beginning."""
    with connection.transaction():
        return connection.execute(
            """
            UPDATE ingest_runs
            SET status = 'running', state = %s, started_at = now(),
                finished_at = NULL, updated_at = now()
            WHERE id = %s
            RETURNING *
            """,
            (Jsonb({"seen": 0, "ingested": 0}), run_id),
        ).fetchone()


def mark_run_running(connection: psycopg.Connection[Any], run_id: int) -> None:
    """Mark an existing run active before resuming it."""
    with connection.transaction():
        connection.execute(
            """
            UPDATE ingest_runs
            SET status = 'running', finished_at = NULL, updated_at = now()
            WHERE id = %s
            """,
            (run_id,),
        )


def ingest_page(
    connection: psycopg.Connection[Any],
    *,
    run_id: int,
    catalog_url: str,
    items: Iterable[dict[str, Any]],
    next_link: dict[str, Any] | None,
    seen: int,
    ingested: int,
    matched: int | None,
) -> None:
    """Upsert one page and checkpoint its successor in one transaction."""
    item_rows = []
    for item in items:
        properties = item.get("properties") or {}
        sensing_start, sensing_end = sensing_times(properties)
        baseline = processing_baseline(properties)
        item_product_type = product_type(properties)
        geometry = item.get("geometry")
        item_rows.append(
            (
                run_id,
                catalog_url,
                item["id"],
                item.get("collection"),
                baseline,
                item_product_type,
                json.dumps(geometry) if geometry is not None else None,
                sensing_start,
                sensing_end,
                Jsonb(item),
            )
        )

    state: dict[str, Any] = {"seen": seen, "ingested": ingested}
    if next_link is not None:
        state["next_link"] = next_link
    if matched is not None:
        state["matched"] = matched

    with connection.transaction():
        if item_rows:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO items (
                        ingest_run_id, catalog_url, stac_id, collection_id,
                        processing_baseline, product_type,
                        geometry, sensing_start, sensing_end, item
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s,
                        ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), %s, %s, %s
                    )
                    ON CONFLICT (catalog_url, stac_id) DO UPDATE SET
                        ingest_run_id = EXCLUDED.ingest_run_id,
                        collection_id = EXCLUDED.collection_id,
                        processing_baseline = EXCLUDED.processing_baseline,
                        product_type = EXCLUDED.product_type,
                        geometry = EXCLUDED.geometry,
                        sensing_start = EXCLUDED.sensing_start,
                        sensing_end = EXCLUDED.sensing_end,
                        item = EXCLUDED.item,
                        ingested_at = now()
                    """,
                    item_rows,
                )
        connection.execute(
            """
            UPDATE ingest_runs
            SET state = %s, updated_at = now()
            WHERE id = %s
            """,
            (Jsonb(state), run_id),
        )


def finish_run(connection: psycopg.Connection[Any], run_id: int) -> None:
    """Mark an ingestion run complete."""
    with connection.transaction():
        connection.execute(
            """
            UPDATE ingest_runs
            SET status = 'completed', finished_at = now(), updated_at = now()
            WHERE id = %s
            """,
            (run_id,),
        )


def fail_run(connection: psycopg.Connection[Any], run_id: int, error: str) -> None:
    """Record a failed crawl while retaining its last checkpoint."""
    with connection.transaction():
        connection.execute(
            """
            UPDATE ingest_runs
            SET status = 'failed',
                state = state || jsonb_build_object('error', %s::text),
                finished_at = now(), updated_at = now()
            WHERE id = %s
            """,
            (error, run_id),
        )


def sensing_times(properties: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    """Extract an Item's instant or interval as timezone-aware datetimes."""
    instant = properties.get("datetime")
    if instant:
        parsed = _parse_datetime(instant)
        return parsed, parsed
    return (
        _parse_datetime(properties.get("start_datetime")),
        _parse_datetime(properties.get("end_datetime")),
    )


def processing_baseline(properties: dict[str, Any]) -> str | None:
    """Extract the processing baseline while preserving textual versions."""
    value = properties.get("version")
    return str(value) if value is not None else None


def product_type(properties: dict[str, Any]) -> str | None:
    """Extract the STAC product type as text."""
    value = properties.get("product:type")
    return str(value) if value is not None else None


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
