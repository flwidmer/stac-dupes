from typing import Any

import pytest
import requests

from stac_dupes import crawler


def test_find_next_link_returns_complete_link() -> None:
    link = {
        "rel": "next",
        "href": "https://example.test/search",
        "method": "POST",
        "body": {"token": "abc"},
        "merge": True,
    }

    assert crawler._find_next_link({"links": [link]}) == link


def test_resumed_pages_request_each_page_once(monkeypatch) -> None:
    links = [
        {"rel": "next", "href": "https://example.test/page/1"},
        {"rel": "next", "href": "https://example.test/page/2"},
    ]
    calls: list[str] = []

    def request_link(session: Any, link: dict, search_body: dict) -> dict:
        calls.append(link["href"])
        index = len(calls)
        return {"features": [], "links": [links[index]] if index < 2 else []}

    monkeypatch.setattr(crawler, "_request_link", request_link)

    assert len(list(crawler._pages_from_link(links[0], {}))) == 2
    assert calls == [
        "https://example.test/page/1",
        "https://example.test/page/2",
    ]


def test_initial_pages_use_retryable_pagination(monkeypatch) -> None:
    next_link = {"rel": "next", "href": "https://example.test/search"}
    first_page = {"features": [{"id": "first"}], "links": [next_link]}
    second_page = {"features": [{"id": "second"}], "links": []}
    run = {
        "catalog_url": "https://example.test",
        "cql2_filter": {},
        "collections": [],
        "page_size": 100,
    }

    class Search:
        def pages_as_dicts(self):
            yield first_page
            raise AssertionError("pystac-client should not request the next page")

    class Client:
        def search(self, **kwargs: Any) -> Search:
            return Search()

    monkeypatch.setattr(crawler.Client, "open", lambda url: Client())
    monkeypatch.setattr(
        crawler,
        "_pages_from_link",
        lambda link, search_body: iter([second_page]),
    )

    assert list(crawler._initial_pages(run)) == [first_page, second_page]


def test_initial_pages_stop_before_ingestion_when_results_exceed_cap(
    monkeypatch,
) -> None:
    next_link = {
        "rel": "next",
        "href": "https://example.test/search",
        "method": "POST",
    }
    first_page = {
        "numberMatched": 239_354,
        "features": [{"id": "first"}],
        "links": [next_link],
    }
    run = {
        "catalog_url": "https://example.test",
        "cql2_filter": {},
        "collections": ["large-collection"],
        "page_size": 100,
    }
    response = requests.Response()
    response.status_code = 422
    response._content = b'{"error":{"message":"startRecord can not exceed 100000."}}'
    request_args: dict[str, Any] = {}

    class Search:
        def pages_as_dicts(self):
            yield first_page

    class Client:
        def search(self, **kwargs: Any) -> Search:
            return Search()

    def request(**kwargs: Any) -> requests.Response:
        request_args.update(kwargs)
        return response

    monkeypatch.setattr(crawler.Client, "open", lambda url: Client())
    monkeypatch.setattr(crawler.requests, "request", request)

    with pytest.raises(
        crawler.CatalogResultLimitError,
        match="matches 239354 items.*first 100000",
    ) as raised:
        list(crawler._initial_pages(run))

    assert request_args["json"]["startRecord"] == 239_354
    assert request_args["json"]["limit"] == 1
    guidance = str(crawler.CrawlError(14, 0, 0, raised.value))
    assert "disjoint CQL2 filters" in guidance
    assert "stac-dupes crawl --run-id 14" not in guidance


def test_request_link_retries_invalid_pagination_token(monkeypatch) -> None:
    invalid = requests.Response()
    invalid.status_code = 400
    invalid._content = b'{"error":{"message":"Invalid token: abc"}}'
    success = requests.Response()
    success.status_code = 200
    success._content = b'{"features":[],"links":[]}'
    responses = [invalid, success]
    sleeps: list[float] = []

    class Session:
        def request(self, **kwargs: Any) -> requests.Response:
            return responses.pop(0)

    monkeypatch.setattr(crawler.time, "sleep", sleeps.append)

    page = crawler._request_link(
        Session(),
        {"href": "https://example.test/search", "body": {"token": "abc"}},
        {},
    )

    assert page == {"features": [], "links": []}
    assert sleeps == [0.25]


def test_request_link_does_not_retry_other_bad_requests(monkeypatch) -> None:
    response = requests.Response()
    response.status_code = 400
    response.url = "https://example.test/search"
    response._content = b'{"error":{"message":"Invalid filter"}}'
    calls = 0

    class Session:
        def request(self, **kwargs: Any) -> requests.Response:
            nonlocal calls
            calls += 1
            return response

    monkeypatch.setattr(crawler.time, "sleep", lambda delay: None)

    with pytest.raises(requests.HTTPError):
        crawler._request_link(Session(), {"href": response.url}, {})

    assert calls == 1


def test_crawl_error_includes_http_details_and_resume_command() -> None:
    request = requests.Request("POST", "https://example.test/search").prepare()
    response = requests.Response()
    response.status_code = 503
    response.reason = "Service Unavailable"
    response.request = request
    response.headers["x-request-id"] = "request-123"
    response._content = b'{"error":"temporarily unavailable"}'
    error = requests.HTTPError("server rejected the request", response=response)

    message = str(crawler.CrawlError(42, 300, 298, error))

    assert "Crawl run 42 failed." in message
    assert "Last saved checkpoint: 300 item(s) seen, 298 item(s) ingested." in message
    assert "Cause: HTTPError: server rejected the request" in message
    assert "Request: POST https://example.test/search" in message
    assert "Response: HTTP 503 Service Unavailable" in message
    assert "Request ID: request-123" in message
    assert 'Response body:\n{"error":"temporarily unavailable"}' in message
    assert "stac-dupes crawl --run-id 42" in message


def test_error_response_body_is_truncated() -> None:
    response = requests.Response()
    response.status_code = 500
    response._content = b"x" * (crawler.MAX_ERROR_BODY_LENGTH + 1)
    error = requests.HTTPError("failed", response=response)

    message = crawler._describe_error(error)

    assert message.endswith("... [truncated]")
    assert len(message) < crawler.MAX_ERROR_BODY_LENGTH + 200


def test_crawl_error_does_not_recommend_resume_past_catalog_limit() -> None:
    response = requests.Response()
    response.status_code = 422
    response._content = (
        b'{"error":{"code":422,"message":"startRecord can not exceed 100000."}}'
    )
    error = requests.HTTPError("unprocessable content", response=response)

    message = str(crawler.CrawlError(13, 100_000, 100_000, error))

    assert "limits a single search to 100,000 records" in message
    assert "disjoint CQL2 filters" in message
    assert "stac-dupes crawl --run-id 13" not in message
