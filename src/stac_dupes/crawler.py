"""Page-oriented STAC crawler with database checkpoints."""

from __future__ import annotations

import re
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
MAX_ERROR_BODY_LENGTH = 2_000
START_RECORD_LIMIT = re.compile(
    r"startRecord\s+can(?:not| not)\s+exceed\s+(\d+)", re.IGNORECASE
)


class CatalogResultLimitError(ValueError):
    """The catalog cannot page through the complete result set."""

    def __init__(self, matched: int, limit: int) -> None:
        super().__init__(
            f"Search matches {matched} items, but the catalog only allows access to "
            f"the first {limit}."
        )
        self.matched = matched
        self.limit = limit


class CrawlError(RuntimeError):
    """A crawl failure with checkpoint and restart information."""

    def __init__(
        self,
        run_id: int,
        seen: int,
        ingested: int,
        error: Exception,
    ) -> None:
        details = [
            f"Crawl run {run_id} failed.",
            f"Last saved checkpoint: {seen} item(s) seen, {ingested} item(s) ingested.",
            _describe_error(error),
            _restart_guidance(run_id, error),
        ]
        super().__init__("\n".join(details))
        self.run_id = run_id


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
    checkpoint_seen = seen
    checkpoint_ingested = ingested
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
            _pages_from_link(next_link, search_body, start_record=seen + 1)
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
            checkpoint_seen = seen
            checkpoint_ingested = ingested
            progress.update(len(page_items))

        db.finish_run(connection, run_id)
    except Exception as error:
        crawl_error = CrawlError(
            run_id,
            checkpoint_seen,
            checkpoint_ingested,
            error,
        )
        db.fail_run(connection, run_id, str(crawl_error))
        raise crawl_error from error
    finally:
        progress.close()

    return CrawlResult(run_id, seen, ingested)


def _initial_pages(run: dict[str, Any]) -> Iterator[dict[str, Any]]:
    client = Client.open(run["catalog_url"])
    pages = _search_pages(client, run)
    first_page = next(pages, None)
    if first_page is None:
        return

    search_body = _search_body(run)
    if _check_result_window(first_page, search_body):
        # MAAP invalidates an existing token when the same search is run again.
        pages = _search_pages(client, run)
        first_page = next(pages, None)
        if first_page is None:
            return

    yield first_page
    next_link = _find_next_link(first_page)
    if next_link is not None:
        yield from _pages_from_link(
            next_link,
            search_body,
            start_record=len(first_page.get("features") or []) + 1,
        )


def _search_pages(client: Client, run: dict[str, Any]) -> Iterator[dict[str, Any]]:
    search_args: dict[str, Any] = {
        "collections": run["collections"] or None,
        "limit": run["page_size"],
    }
    if run["cql2_filter"]:
        search_args["filter"] = run["cql2_filter"]
        search_args["filter_lang"] = "cql2-json"
    return client.search(**search_args).pages_as_dicts()


def _pages_from_link(
    link: dict[str, Any],
    search_body: dict[str, Any],
    *,
    start_record: int = 1,
) -> Iterator[dict[str, Any]]:
    session = requests.Session()
    current: dict[str, Any] | None = link
    while current is not None:
        try:
            page = _request_link(session, current, search_body)
        except requests.HTTPError as error:
            if error.response is None or not _is_invalid_token_response(error.response):
                raise
            page = _request_offset(session, current, search_body, start_record)
        yield page
        start_record += len(page.get("features") or [])
        current = _find_next_link(page)


def _request_offset(
    session: requests.Session,
    link: dict[str, Any],
    search_body: dict[str, Any],
    start_record: int,
) -> dict[str, Any]:
    offset_link = {
        **link,
        "body": {**search_body, "startRecord": start_record},
        "merge": False,
    }
    return _request_link(session, offset_link, {})


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


def _check_result_window(page: dict[str, Any], search_body: dict[str, Any]) -> bool:
    """Probe whether the catalog can reach the final matched record."""
    matched = page.get("numberMatched")
    next_link = _find_next_link(page)
    if not isinstance(matched, int) or next_link is None:
        return False

    method = str(next_link.get("method", "GET")).upper()
    request_args: dict[str, Any] = {
        "method": method,
        "url": next_link["href"],
        "headers": next_link.get("headers"),
        "timeout": 60,
    }
    probe_body = {**search_body, "limit": 1, "startRecord": matched}
    if method == "GET":
        request_args["params"] = probe_body
    else:
        request_args["json"] = probe_body
    try:
        response = requests.request(**request_args)
    except requests.RequestException:
        return False

    limit = _start_record_limit(response)
    if limit is not None and matched > limit:
        raise CatalogResultLimitError(matched, limit)
    return True


def _describe_error(error: Exception) -> str:
    """Return actionable request details without exposing request headers or bodies."""
    details = [f"Cause: {type(error).__name__}: {error}"]
    if not isinstance(error, requests.RequestException):
        return "\n".join(details)

    response = error.response
    request = error.request or (response.request if response is not None else None)
    if request is not None:
        details.append(f"Request: {request.method} {request.url}")
    if response is not None:
        status = f"{response.status_code} {response.reason or ''}".rstrip()
        details.append(f"Response: HTTP {status}")
        request_id = next(
            (
                response.headers[name]
                for name in ("x-request-id", "x-amzn-requestid", "traceparent")
                if name in response.headers
            ),
            None,
        )
        if request_id:
            details.append(f"Request ID: {request_id}")
        body = response.text.strip()
        if body:
            if len(body) > MAX_ERROR_BODY_LENGTH:
                body = f"{body[:MAX_ERROR_BODY_LENGTH]}... [truncated]"
            details.append(f"Response body:\n{body}")
    return "\n".join(details)


def _restart_guidance(run_id: int, error: Exception) -> str:
    limit = _result_limit_from_error(error)
    if limit is not None:
        return (
            f"This catalog limits a single search to {limit:,} records, so resuming "
            "or re-crawling this run will fail again. Start new runs with disjoint "
            f"CQL2 filters that each match no more than {limit:,} items."
        )
    return (
        "Resume from the last saved checkpoint with:\n"
        f"  stac-dupes crawl --run-id {run_id}"
    )


def _result_limit_from_error(error: Exception) -> int | None:
    if isinstance(error, CatalogResultLimitError):
        return error.limit
    if not isinstance(error, requests.HTTPError) or error.response is None:
        return None

    return _start_record_limit(error.response)


def _start_record_limit(response: requests.Response) -> int | None:
    try:
        payload = response.json()
    except requests.JSONDecodeError:
        return None
    api_error = payload.get("error") if isinstance(payload, dict) else None
    message = api_error.get("message") if isinstance(api_error, dict) else None
    if not isinstance(message, str):
        return None
    match = START_RECORD_LIMIT.search(message)
    return int(match.group(1)) if match else None


def _find_next_link(page: dict[str, Any]) -> dict[str, Any] | None:
    for link in page.get("links") or []:
        if link.get("rel") == "next":
            return link
    return None


def _search_body(run: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "limit": run["page_size"],
    }
    if run["cql2_filter"]:
        body["filter"] = run["cql2_filter"]
        body["filter-lang"] = "cql2-json"
    if run["collections"]:
        body["collections"] = run["collections"]
    return body
