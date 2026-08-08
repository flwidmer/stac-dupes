from typing import Any

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
