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
