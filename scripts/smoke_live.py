#!/usr/bin/env python3
"""A smoke check of a running LLeMbas after a deploy: is the live path whole?

    LEMBAS_SMOKE_TOKEN=lmb_... python scripts/smoke_live.py https://llembas.example MODEL
    python scripts/smoke_live.py URL MODEL --token-env MY_TOKEN_VAR --timeout 180

What it does, in order, and stops at the first failure:

1. `GET /healthz` -- the process is up and reaches its database.
2. `GET /.well-known/lembas.json` -- the version it runs (printed).
3. `GET /api/v2/models` -- the token's models; MODEL must be one of them (its
   id or its display name).
4. `POST /api/v2/chats` -- one short, temporary chat on MODEL ("Reply with the
   single word: ok"), and its reply followed on
   `/api/v2/chats/{chat}/messages/{reply}/stream`: the stream must carry a
   `status` frame with `progress` beside its text (harness spec 3.4.0), a
   `progress` that is null or names one of the spec's phases, a `done` frame
   with a finished reply, and `close`.
5. `DELETE /api/v2/chats/{chat}` -- that chat goes again, whatever happened.

It changes nothing else: one chat, made and deleted, with the token's own
rights. The token is an app token (`app` scope) of an account that may use
MODEL; it is read from an environment variable (`LEMBAS_SMOKE_TOKEN` by
default) and never printed, logged or written anywhere. Only the standard
library: it runs wherever Python does.

Exit status: 0 when every check passed, 1 when one failed, 2 for bad usage.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from typing import Any

PHASES = {
    "queued", "waiting_for_model", "loading_model", "reading_prompt", "thinking", "writing",
    "running_tool", "waiting_for_person", "waiting_for_subagents", "compacting", "retrying",
    "stopped", "failed",
}
PROMPT = "Reply with the single word: ok"


class Failed(Exception):
    """A check that did not pass; the message says which and why."""


class Client:
    def __init__(self, base: str, token: str, timeout: float, insecure: bool) -> None:
        self.base = base.rstrip("/")
        self._token = token
        self.timeout = timeout
        self.context = ssl._create_unverified_context() if insecure else None

    def _request(self, method: str, path: str, body: Any = None, *, auth: bool = True):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("User-Agent", "lembas-smoke-live")
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if auth:
            request.add_header("Authorization", f"Bearer {self._token}")
        return urllib.request.urlopen(request, timeout=self.timeout, context=self.context)

    def call(self, method: str, path: str, body: Any = None, *, auth: bool = True) -> Any:
        try:
            with self._request(method, path, body, auth=auth) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode("utf-8", "replace")
            raise Failed(f"{method} {path}: HTTP {exc.code} {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise Failed(f"{method} {path}: {exc}") from None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return raw.decode("utf-8", "replace")

    def stream(self, path: str):
        """Server-sent events as (name, data) pairs, until the server ends."""
        try:
            response = self._request("GET", path)
        except urllib.error.HTTPError as exc:
            raise Failed(f"GET {path}: HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise Failed(f"GET {path}: {exc}") from None
        with response:
            name, data = "", []
            for raw in response:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not line:
                    if name:
                        yield name, "\n".join(data)
                    name, data = "", []
                elif line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].removeprefix(" "))


def say(ok: bool, what: str) -> None:
    print(f"{'ok  ' if ok else 'FAIL'}  {what}", flush=True)


def check_health(client: Client) -> None:
    answer = client.call("GET", "/healthz", auth=False)
    say(True, f"healthz: {str(answer)[:60]}")


def check_version(client: Client) -> str:
    found = client.call("GET", "/.well-known/lembas.json", auth=False)
    if not isinstance(found, dict) or found.get("service") != "lembas":
        raise Failed("/.well-known/lembas.json does not say this is LLeMbas")
    version = str(found.get("version") or "?")
    say(True, f"version: {version}")
    return version


def pick_model(client: Client, wanted: str) -> str:
    listed = client.call("GET", "/api/v2/models")
    models = (listed or {}).get("models") if isinstance(listed, dict) else None
    if not isinstance(models, list) or not models:
        raise Failed("/api/v2/models lists no model for this token")
    for model in models:
        if wanted in (model.get("id"), model.get("name")):
            say(True, f"models: {len(models)}, {wanted} among them")
            return str(model["id"])
    raise Failed(f"/api/v2/models: no model {wanted!r} for this token")


def check_chat(client: Client, model: str, timeout: float) -> str | None:
    """One short chat, followed to its end. Returns its id for deleting, even
    when a check after its creation fails (raised after the id is known)."""
    made = client.call(
        "POST", "/api/v2/chats", {"text": PROMPT, "model": model, "temporary": True}
    )
    chat_id = str(((made or {}).get("chat") or {}).get("id") or "")
    reply_id = str(((made or {}).get("reply") or {}).get("id") or "")
    if not chat_id or not reply_id:
        raise Failed("POST /api/v2/chats: no chat and reply in the answer")
    say(True, "chat made")
    try:
        _follow(client, chat_id, reply_id, timeout)
    except Failed as exc:
        raise ChatFailed(chat_id, str(exc)) from None
    return chat_id


class ChatFailed(Failed):
    def __init__(self, chat_id: str, message: str) -> None:
        super().__init__(message)
        self.chat_id = chat_id


def _follow(client: Client, chat_id: str, reply_id: str, timeout: float) -> None:
    started = time.monotonic()
    seen: list[str] = []
    statuses = 0
    phases: set[str] = set()
    done: dict | None = None
    path = f"/api/v2/chats/{chat_id}/messages/{reply_id}/stream"
    for name, data in client.stream(path):
        seen.append(name)
        if time.monotonic() - started > timeout:
            raise Failed(f"the stream ran past {timeout:.0f} s")
        try:
            payload = json.loads(data) if data else None
        except ValueError:
            raise Failed(f"a `{name}` frame is not JSON") from None
        if name == "status":
            if not isinstance(payload, dict) or "text" not in payload or "progress" not in payload:
                raise Failed("a `status` frame without `text` and `progress` beside it")
            statuses += 1
            progress = payload["progress"]
            if progress is not None:
                if not isinstance(progress, dict) or progress.get("phase") not in PHASES:
                    raise Failed(f"`progress` names no phase of the spec: {progress!r:.120}")
                if not isinstance(progress.get("since"), int):
                    raise Failed("`progress.since` is not Unix milliseconds")
                phases.add(progress["phase"])
        elif name == "done":
            done = payload if isinstance(payload, dict) else {}
        elif name == "close":
            break
    if not statuses:
        raise Failed(f"no `status` frame in the stream (frames: {', '.join(seen[:12])})")
    say(True, f"stream: {statuses} status frames; phases seen: {', '.join(sorted(phases)) or '-'}")
    if done is None:
        raise Failed("the stream ended without a `done` frame")
    message = done.get("message") or {}
    if not message.get("complete", True):
        raise Failed("`done` carries an unfinished reply")
    if message.get("error"):
        raise Failed(f"the reply failed: {str(message['error'])[:200]}")
    text = "".join(
        str(step.get("markdown") or "")
        for step in message.get("steps") or []
        if isinstance(step, dict) and step.get("kind") == "text"
    )
    say(True, f"reply in {time.monotonic() - started:.1f} s: {text.strip()[:40]!r}")


def delete(client: Client, chat_id: str) -> bool:
    try:
        client.call("DELETE", f"/api/v2/chats/{chat_id}")
    except Failed as exc:
        say(False, f"chat not deleted: {exc}")
        return False
    say(True, "chat deleted")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("url", help="the instance's address, e.g. https://llembas.example")
    parser.add_argument("model", help="a model the token may use: its id or display name")
    parser.add_argument("--token-env", default="LEMBAS_SMOKE_TOKEN",
                        help="the environment variable holding the app token")
    parser.add_argument("--timeout", type=float, default=120.0,
                        help="seconds for each request and for the reply (default 120)")
    parser.add_argument("--insecure", action="store_true",
                        help="do not verify the TLS certificate (an internal CA)")
    args = parser.parse_args()
    token = os.environ.get(args.token_env, "").strip()
    if not token:
        print(f"smoke_live: set {args.token_env} to an app token", file=sys.stderr)
        return 2
    if not args.url.startswith(("http://", "https://")):
        print("smoke_live: the URL must start with http:// or https://", file=sys.stderr)
        return 2
    client = Client(args.url, token, args.timeout, args.insecure)
    chat_id: str | None = None
    failed = False
    try:
        check_health(client)
        check_version(client)
        model = pick_model(client, args.model)
        chat_id = check_chat(client, model, args.timeout)
    except ChatFailed as exc:
        chat_id = exc.chat_id
        say(False, str(exc))
        failed = True
    except Failed as exc:
        say(False, str(exc))
        failed = True
    if chat_id and not delete(client, chat_id):
        failed = True
    print("smoke: FAILED" if failed else "smoke: passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
