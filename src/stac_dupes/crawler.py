"""Page-oriented STAC crawler with database checkpoints."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import psycopg
import requests
from pystac_client import Client
from tqdm import tqdm

from stac_dupes import db

INVALID_TOKEN_ATTEMPTS = 5


@dataclass(frozen=True)
class CrawlResult:
    """Summary of a completed ingestion run."""

    run_id: int
    seen: int
    ingested: int


def crawl(
    connection: psycopg.Connection[Any],
    *,
    catalog_url: str | None = None,
    cql2_filter: dict[str, Any] | None = None,
    collections: list[str] | None = None,
    page_size: int = 100,
    run_id: int | None = None,
    re_crawl: bool = False,
    show_progress: bool = True,
) -> CrawlResult:
    """Start, resume, or re-crawl a STAC ingestion run."""
    if run_id is None:
        if catalog_url is None or cql2_filter is None:
            raise ValueError("catalog_url and cql2_filter are required for a new run")
        run = db.create_run(
            connection,
            catalog_url.rstrip("/"),
            cql2_filter,
            collections or [],
            page_size,
        )
    else:
        run = db.get_run(connection, run_id)
        if run is None:
            raise ValueError(f"ingestion run {run_id} does not exist")
        if run["status"] == "completed" and not re_crawl:
            state = run["state"]
            return CrawlResult(run_id, state.get("seen", 0), state.get("ingested", 0))
        run = db.reset_run(connection, run_id) if re_crawl else run
        if not re_crawl:
            db.mark_run_running(connection, run_id)

    run_id = run["id"]
    state = run["state"]
    seen = int(state.get("seen", 0))
    ingested = int(state.get("ingested", 0))
    matched = state.get("matched")
    next_link = state.get("next_link")
    search_body = _search_body(run)

    progress = tqdm(
        total=matched,
        initial=seen,
        unit="item",
        desc=f"run {run_id}",
        disable=not show_progress,
    )
    try:
        pages = (
            _pages_from_link(next_link, search_body)
            if next_link is not None
            else _initial_pages(run)
        )
        for page in pages:
            page_items = page.get("features") or []
            page_matched = page.get("numberMatched")
            if page_matched is not None:
                matched = int(page_matched)
                progress.total = matched
                progress.refresh()

            seen += len(page_items)
            ingested += len(page_items)
            next_link = _find_next_link(page)
            db.ingest_page(
                connection,
                run_id=run_id,
                catalog_url=run["catalog_url"],
                items=page_items,
                next_link=next_link,
                seen=seen,
                ingested=ingested,
                matched=matched,
            )
            progress.update(len(page_items))

        db.finish_run(connection, run_id)
    except Exception as error:
        db.fail_run(connection, run_id, str(error))
        raise
    finally:
        progress.close()

    return CrawlResult(run_id, seen, ingested)


def _initial_pages(run: dict[str, Any]) -> Iterator[dict[str, Any]]:
    client = Client.open(run["catalog_url"])
    search = client.search(
        filter=run["cql2_filter"],
        filter_lang="cql2-json",
        collections=run["collections"] or None,
        limit=run["page_size"],
    )
    pages = search.pages_as_dicts()
    first_page = next(pages, None)
    if first_page is None:
        return

    yield first_page
    next_link = _find_next_link(first_page)
    if next_link is not None:
        yield from _pages_from_link(next_link, _search_body(run))


def _pages_from_link(
    link: dict[str, Any], search_body: dict[str, Any]
) -> Iterator[dict[str, Any]]:
    session = requests.Session()
    current: dict[str, Any] | None = link
    while current is not None:
        page = _request_link(session, current, search_body)
        yield page
        current = _find_next_link(page)


def _request_link(
    session: requests.Session,
    link: dict[str, Any],
    search_body: dict[str, Any],
) -> dict[str, Any]:
    method = str(link.get("method", "GET")).upper()
    body = link.get("body") or {}
    if link.get("merge"):
        body = {**search_body, **body}

    request_args: dict[str, Any] = {
        "method": method,
        "url": link["href"],
        "headers": link.get("headers"),
        "timeout": 60,
    }
    if method == "GET":
        request_args["params"] = body
    else:
        request_args["json"] = body
    for attempt in range(INVALID_TOKEN_ATTEMPTS):
        response = session.request(**request_args)
        if (
            not _is_invalid_token_response(response)
            or attempt == INVALID_TOKEN_ATTEMPTS - 1
        ):
            response.raise_for_status()
            return response.json()
        time.sleep(0.25 * 2**attempt)

    raise AssertionError("pagination retry loop did not return")


def _is_invalid_token_response(response: requests.Response) -> bool:
    """Identify the transient pagination-token response returned by MAAP."""
    if response.status_code != 400:
        return False
    try:
        payload = response.json()
    except requests.JSONDecodeError:
        return False
    error = payload.get("error") if isinstance(payload, dict) else None
    return isinstance(error, dict) and str(error.get("message", "")).startswith(
        "Invalid token:"
    )


def _find_next_link(page: dict[str, Any]) -> dict[str, Any] | None:
    for link in page.get("links") or []:
        if link.get("rel") == "next":
            return link
    return None


def _search_body(run: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "filter": run["cql2_filter"],
        "filter-lang": "cql2-json",
        "limit": run["page_size"],
    }
    if run["collections"]:
        body["collections"] = run["collections"]
    return body
