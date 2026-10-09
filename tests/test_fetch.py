"""Fetching a web page, and refusing to fetch the wrong ones.

The refusals are the important half. This runs on a server that can reach the
router, the other services on the box, and LLeMbas itself — and the URL can come
from a model, which can be talked into things by a page it just read.
"""

from __future__ import annotations

import httpx
import pytest

from lembas.services.fetch import FetchError, check_url, fetch, html_to_text


# --- What is refused ---------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/admin",   # LLeMbas itself
        "http://localhost/",             # the same, by name
        "http://10.1.2.3/",              # the network the server is on
        "http://192.168.1.1/",           # a router
        "http://172.16.5.4/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata, i.e. credentials
        "http://[::1]/",
        "http://0.0.0.0/",
    ],
)
def test_private_addresses_are_refused(url):
    with pytest.raises(FetchError, match="private or local"):
        check_url(url)


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "ftp://host/x", "gopher://host/", "javascript:alert(1)"]
)
def test_only_http_and_https(url):
    with pytest.raises(FetchError, match="http"):
        check_url(url)


def test_a_url_with_no_host_is_refused():
    with pytest.raises(FetchError):
        check_url("http:///nothing")


def test_the_check_is_on_the_resolved_address(monkeypatch):
    """A hostname pointing at 127.0.0.1 is the obvious way past a check that
    only reads the text of the URL."""
    import socket

    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 80))]
    )
    with pytest.raises(FetchError, match="private or local"):
        check_url("http://sneaky.example.com/")


def test_one_private_address_among_several_is_still_refused(monkeypatch):
    """A name resolving to one public and one private address must not be
    usable to reach the private one."""
    import socket

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 80)), (2, 1, 6, "", ("10.1.2.3", 80))],
    )
    with pytest.raises(FetchError, match="private or local"):
        check_url("http://mixed.example.com/")


def test_an_administrator_can_open_it_deliberately():
    assert check_url("http://127.0.0.1:8080/", allow_private=True)


def test_a_public_address_passes(monkeypatch):
    import socket

    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 80))]
    )
    assert check_url("https://example.com/x") == "https://example.com/x"


# --- Redirects ---------------------------------------------------------------
async def test_a_redirect_to_a_private_address_is_refused(mock_http, monkeypatch):
    """httpx's own following would validate the first address and then happily
    land on localhost, which is why redirects are followed by hand."""
    import socket

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 80))]
        if host == "example.com"
        else [(2, 1, 6, "", ("127.0.0.1", 80))],
    )
    mock_http(
        lambda _r: httpx.Response(302, headers={"location": "http://127.0.0.1:8080/admin"})
    )
    with pytest.raises(FetchError, match="private or local"):
        await fetch("https://example.com/")


async def test_endless_redirects_end(mock_http, monkeypatch):
    import socket

    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 80))]
    )
    mock_http(lambda r: httpx.Response(302, headers={"location": str(r.url)}))
    with pytest.raises(FetchError, match="redirected too many"):
        await fetch("https://example.com/")


# --- Reducing a page ---------------------------------------------------------
def test_script_and_style_are_dropped():
    _, text = html_to_text(
        "<html><body><script>alert(1)</script><style>p{}</style><p>Real text.</p></body></html>"
    )
    assert text == "Real text."


def test_the_title_is_taken_and_not_repeated_in_the_body():
    title, text = html_to_text(
        "<html><head><title> A  Page </title></head><body><p>Body.</p></body></html>"
    )
    assert title == "A Page"
    assert text == "Body."


def test_block_tags_become_line_breaks():
    """Without this the whole page arrives as one paragraph."""
    _, text = html_to_text("<p>One</p><p>Two</p><li>Three</li>")
    assert text.splitlines() == ["One", "Two", "Three"]


def test_entities_are_unescaped():
    _, text = html_to_text("<p>Salt &amp; pepper, 5 &gt; 3</p>")
    assert text == "Salt & pepper, 5 > 3"


# --- Fetching ----------------------------------------------------------------
async def test_a_page_is_reduced_to_text(mock_http, monkeypatch):
    import socket

    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 80))]
    )
    mock_http(
        lambda _r: httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                "<html><head><title>Mallorn</title></head>"
                "<body><p>A golden tree.</p></body></html>"
            ),
        )
    )
    page = await fetch("https://example.com/mallorn")
    assert page.title == "Mallorn"
    assert page.text == "A golden tree."


async def test_a_binary_response_is_refused_with_advice(mock_http, monkeypatch):
    import socket

    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 80))]
    )
    mock_http(
        lambda _r: httpx.Response(200, headers={"content-type": "image/png"}, content=b"\x89PNG")
    )
    with pytest.raises(FetchError, match="Attach it as a file"):
        await fetch("https://example.com/x.png")


async def test_a_page_with_no_readable_text_says_so(mock_http, monkeypatch):
    import socket

    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 80))]
    )
    mock_http(
        lambda _r: httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><body><div id=app></div></body></html>",
        )
    )
    with pytest.raises(FetchError, match="JavaScript"):
        await fetch("https://example.com/")


# --- Content types that are text without being text/* ------------------------------
@pytest.fixture
def public(monkeypatch):
    import socket

    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 80))]
    )


@pytest.mark.parametrize(
    "content_type",
    ["application/json", "application/vnd.api+json", "application/xml", "application/yaml"],
)
async def test_a_json_or_xml_document_comes_back_verbatim(mock_http, public, content_type):
    """The sniff was written for "save this page into my library" and refused
    every JSON API there is -- already wrong for the link-attach path, and
    unusable once a model can ask for a URL itself."""
    mock_http(
        lambda _r: httpx.Response(
            200, headers={"content-type": content_type}, text='{"ok": true}'
        )
    )
    page = await fetch("https://example.com/api")
    assert page.text == '{"ok": true}'


@pytest.mark.parametrize("content_type", ["image/png", "application/pdf",
                                          "application/octet-stream"])
async def test_binary_is_still_refused(mock_http, public, content_type):
    """The widening is exactly one list plus two suffixes. Handing a model five
    megabytes of binary is the thing the refusal was for."""
    mock_http(
        lambda _r: httpx.Response(200, headers={"content-type": content_type}, content=b"\x00\x01")
    )
    with pytest.raises(FetchError, match="Attach it as a file"):
        await fetch("https://example.com/x")
