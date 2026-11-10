"""`scripts/smoke_live.py`, run against this app through the test client
(never a live instance): every check passes on a working server, the token is
never printed, a broken stream fails it, and the chat is deleted either way."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from tests.test_api_v2 import _bearer, _models, _upstream

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "smoke_live.py"


def _load():
    spec = importlib.util.spec_from_file_location("smoke_live", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _client(smoke, client, headers, *, break_status=False):
    class Through(smoke.Client):
        def call(self, method, path, body=None, *, auth=True):
            got = client.request(method, path, json=body, headers=headers if auth else {})
            if got.status_code >= 400:
                raise smoke.Failed(f"{method} {path}: HTTP {got.status_code}")
            try:
                return got.json()
            except ValueError:
                return got.text

        def stream(self, path):
            text = client.get(path, headers=headers).text
            for block in text.split("\n\n"):
                lines = block.splitlines()
                name = next((x[6:].strip() for x in lines if x.startswith("event:")), "")
                data = "\n".join(x[5:].removeprefix(" ") for x in lines if x.startswith("data:"))
                if name == "status" and break_status:
                    data = json.dumps({"text": ""})
                if name:
                    yield name, data

    return Through("http://testserver", headers["Authorization"][7:], 30, False)


def test_a_working_server_passes_and_the_chat_goes(client, db, registered, mock_http, capsys):
    from lembas.db.models import Chat

    smoke = _load()
    _models(db)
    mock_http(_upstream("ok"))
    headers = _bearer(db)
    through = _client(smoke, client, headers)
    smoke.check_health(through)
    smoke.check_version(through)
    model = smoke.pick_model(through, "qwen")
    chat_id = smoke.check_chat(through, model, 30)
    assert smoke.delete(through, chat_id)
    db.expire_all()
    assert db.get(Chat, chat_id) is None
    out = capsys.readouterr().out
    assert "FAIL" not in out and "reply in" in out
    assert headers["Authorization"][7:] not in out


def test_a_status_frame_without_progress_fails(client, db, registered, mock_http):
    import pytest

    smoke = _load()
    _models(db)
    mock_http(_upstream("ok"))
    headers = _bearer(db)
    through = _client(smoke, client, headers, break_status=True)
    with pytest.raises(smoke.ChatFailed) as failed:
        smoke.check_chat(through, smoke.pick_model(through, "qwen"), 30)
    assert "progress" in str(failed.value) and failed.value.chat_id


def test_no_token_is_bad_usage(monkeypatch):
    smoke = _load()
    monkeypatch.delenv("LEMBAS_SMOKE_TOKEN", raising=False)
    monkeypatch.setattr("sys.argv", ["smoke_live.py", "https://x.example", "m"])
    assert smoke.main() == 2


def test_the_script_names_no_private_host():
    text = SCRIPT.read_text(encoding="utf-8").lower()
    for word in (".lan", "10.0.", "192.168."):
        assert word not in text
