"""Web search providers and their normalisation."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from lembas.services import search as search_service
from lembas.services import settings_store
from lembas.services.crypto import encrypt
from lembas.services.search import firecrawl, searxng
from lembas.services.search.base import SearchError, SearchResult, clean


# --- What a result is --------------------------------------------------------
def test_only_http_urls_are_linkable():
    """A search provider is an untrusted source. A result carrying a
    javascript: URL must never become an anchor pointing at it."""
    assert SearchResult("t", "https://example.com", "").is_linkable
    assert SearchResult("t", "http://example.com", "").is_linkable
    assert not SearchResult("t", "javascript:alert(1)", "").is_linkable
    assert not SearchResult("t", "data:text/html,<script>", "").is_linkable


def test_the_host_is_pulled_out_for_display():
    assert SearchResult("t", "https://en.wikipedia.org/wiki/X", "").host == "en.wikipedia.org"


def test_clean_drops_a_row_with_no_url():
    assert clean("A title", "", "text") is None


def test_clean_falls_back_to_the_url_as_a_title():
    assert clean("", "https://example.com", "").title == "https://example.com"


def test_clean_collapses_whitespace_and_truncates():
    from lembas.services.search.base import MAX_SNIPPET

    result = clean("  a\n  b  ", "https://x.test", "word " * 500)
    assert result.title == "a b"
    assert len(result.snippet) <= MAX_SNIPPET


# --- SearXNG -----------------------------------------------------------------
async def test_searxng_normalises_its_results(mock_http):
    mock_http(
        lambda _r: httpx.Response(
            200,
            json={
                "results": [
                    {"title": "One", "url": "https://one.test", "content": "first"},
                    {"title": "Two", "url": "https://two.test", "content": "second"},
                ]
            },
        )
    )
    results = await searxng.search({"searxng_base_url": "http://searx"}, "q", 5)
    assert [r.title for r in results] == ["One", "Two"]
    assert results[0].snippet == "first"


async def test_searxng_names_the_disabled_json_format(mock_http):
    """A stock instance refuses the JSON format with a 403. Reporting "search
    failed" would leave the one-line fix undiscoverable."""
    mock_http(lambda _r: httpx.Response(403, text="Forbidden"))
    with pytest.raises(SearchError) as caught:
        await searxng.search({"searxng_base_url": "http://searx"}, "q", 5)
    assert "settings.yml" in caught.value.message


async def test_searxng_treats_an_html_answer_as_the_same_misconfiguration(mock_http):
    mock_http(lambda _r: httpx.Response(200, text="<html>results</html>"))
    with pytest.raises(SearchError) as caught:
        await searxng.search({"searxng_base_url": "http://searx"}, "q", 5)
    assert "settings.yml" in caught.value.message


async def test_searxng_needs_a_url():
    with pytest.raises(SearchError):
        await searxng.search({"searxng_base_url": ""}, "q", 5)


# --- Firecrawl ---------------------------------------------------------------
@pytest.mark.parametrize(
    "payload",
    [
        {"data": [{"title": "One", "url": "https://one.test", "description": "first"}]},
        # Newer responses nest the list under data.web.
        {"data": {"web": [{"title": "One", "url": "https://one.test", "description": "first"}]}},
    ],
)
async def test_firecrawl_reads_both_response_shapes(mock_http, payload):
    mock_http(lambda _r: httpx.Response(200, json=payload))
    results = await firecrawl.search(
        {"firecrawl_api_key_encrypted": encrypt("fc-x")}, "q", 5
    )
    assert [r.url for r in results] == ["https://one.test"]


async def test_firecrawl_reports_a_rejected_key(mock_http):
    mock_http(lambda _r: httpx.Response(401, json={"error": "bad key"}))
    with pytest.raises(SearchError) as caught:
        await firecrawl.search({"firecrawl_api_key_encrypted": encrypt("fc-x")}, "q", 5)
    assert "rejected" in caught.value.message


async def test_firecrawl_needs_a_key():
    with pytest.raises(SearchError):
        await firecrawl.search({"firecrawl_api_key_encrypted": ""}, "q", 5)


# --- Dispatch ----------------------------------------------------------------
async def test_run_refuses_an_empty_query():
    with pytest.raises(SearchError):
        await search_service.run({"provider": "ddgs"}, "   ")


async def test_run_refuses_an_unknown_provider():
    with pytest.raises(SearchError):
        await search_service.run({"provider": "askjeeves"}, "q")


async def test_the_administrators_limit_is_a_ceiling(mock_http):
    """A model asking for fifty results is asking for a prompt nobody can
    afford."""
    rows = [{"title": f"r{i}", "url": f"https://x{i}.test", "content": ""} for i in range(50)]
    mock_http(lambda _r: httpx.Response(200, json={"results": rows}))

    config = {"provider": "searxng", "searxng_base_url": "http://searx", "max_results": 3}
    assert len(await search_service.run(config, "q", limit=50)) == 3


# --- Admin -------------------------------------------------------------------
def test_search_settings_round_trip(client: TestClient, db, registered):
    client.post(
        "/admin/search",
        data={
            "enabled": "true",
            "provider": "searxng",
            "max_results": "7",
            "region": "uk-en",
            "safesearch": "strict",
            "searxng_base_url": "http://searx:8888/",
            "timeout": "30",
        },
        follow_redirects=False,
    )
    values = settings_store.search(db)
    assert values["enabled"] is True
    assert values["provider"] == "searxng"
    assert values["max_results"] == 7
    # Trailing slash stripped, so the provider does not build a double slash.
    assert values["searxng_base_url"] == "http://searx:8888"


def test_an_unknown_provider_falls_back_to_the_default(client: TestClient, db, registered):
    client.post(
        "/admin/search", data={"provider": "askjeeves"}, follow_redirects=False
    )
    assert settings_store.search(db)["provider"] == "ddgs"


def test_results_per_search_is_bounded(client: TestClient, db, registered):
    client.post("/admin/search", data={"max_results": "500"}, follow_redirects=False)
    assert settings_store.search(db)["max_results"] == 20


def test_the_search_page_renders_every_provider(client: TestClient, registered):
    page = client.get("/admin/search").text
    for provider in search_service.PROVIDERS:
        assert provider.label in page
