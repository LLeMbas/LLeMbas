"""One run against a real server on a real socket.

Every other HTTP test in this suite goes through `TestClient`, which is an
in-process ASGI call: no socket, no uvicorn, no HTTP parsing. That covers the
application and covers nothing about the thing `deploy/install.sh` actually
starts. Uvicorn's own behaviour -- how it frames a streaming response, whether
it holds a connection open, what it does on shutdown with a stream still
running -- is what a deployment depends on and what nothing here touched.

Deliberately **one** test file and a handful of assertions. This is a smoke
test: it is here so that "the server starts and serves" is a fact rather than an
inference, not to re-test the application through a slower transport.

Marked `slow` because it binds a port and waits for a process.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time

import httpx
import pytest

pytestmark = pytest.mark.slow


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    """A real `lembas-server serve`, on a real port, with its own data directory.

    Started as a subprocess rather than a thread: the point is the process the
    unit file starts, and an in-thread uvicorn shares this interpreter's already
    imported settings singleton.
    """
    port = _free_port()
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "LEMBAS_DATA_DIR": str(tmp_path),
        "LEMBAS_HOST": "127.0.0.1",
        "LEMBAS_PORT": str(port),
        "LEMBAS_SECRET_KEY": "t" * 44,
        "LEMBAS_ALLOW_SIGNUP": "true",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "lembas.cli", "serve"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError(f"the server exited: {process.stdout.read()}")
            try:
                httpx.get(f"{base}/healthz", timeout=1.0)
                break
            except httpx.TransportError:
                time.sleep(0.2)
        else:  # pragma: no cover - only on a machine that cannot start it
            raise AssertionError("the server never answered")
        yield base
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            process.kill()


def test_it_starts_and_answers(server):
    """`lembas-server serve` is what the systemd unit runs, and until now nothing
    checked that the command in the unit file works at all."""
    response = httpx.get(f"{server}/healthz", timeout=10.0)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_a_signed_out_visitor_is_sent_to_the_sign_in_page(server):
    """Over real HTTP, with a real redirect, because the redirect is a header
    and headers are the part `TestClient` does not have to get right."""
    response = httpx.get(f"{server}/", follow_redirects=False, timeout=10.0)

    assert response.status_code in (302, 303, 307)
    assert "/auth/login" in response.headers["location"]


def test_the_stylesheets_it_serves_are_the_ones_in_the_tree(server):
    """The static mount, which is configuration rather than code and therefore
    fails at deploy time rather than in a unit test."""
    response = httpx.get(f"{server}/static/css/app.css", timeout=10.0)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert ".sidebar" in response.text


def test_a_fresh_instance_offers_a_way_in(server):
    """The first thing a new deployment does. On an empty database `/auth/login`
    redirects to registration, because an instance with no accounts and a sign-in
    form is a door with no key -- and the first account made becomes the
    administrator.

    Worth having over real HTTP: this is the exact path somebody walks thirty
    seconds after `install.sh` finishes, and it is a chain of redirects, which is
    the part that is headers rather than code.
    """
    response = httpx.get(f"{server}/auth/login", follow_redirects=True, timeout=10.0)

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "register" in str(response.url) or "register" in response.text.lower()


def test_an_account_can_be_created_and_used_over_real_http(server):
    """Registration, the session cookie, and a page that needs it -- end to end
    through uvicorn. A cookie is `Set-Cookie` plus the browser sending it back,
    and both halves are transport rather than application: `TestClient` has its
    own cookie jar and would pass whatever the header said.
    """
    with httpx.Client(base_url=server, timeout=15.0, follow_redirects=True) as client:
        made = client.post(
            "/auth/register",
            data={
                "name": "Frodo",
                "email": "f@shire.test",
                "password": "speak-friend-and-enter",
                "confirm": "speak-friend-and-enter",
            },
        )
        assert made.status_code == 200, made.text[:400]
        assert client.cookies, "no session cookie came back"

        # And the cookie actually admits us to something that requires one.
        chat = client.get("/chat")
        assert chat.status_code == 200
        assert "auth/login" not in str(chat.url)
