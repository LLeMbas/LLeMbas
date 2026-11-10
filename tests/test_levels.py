"""Effort levels, the thinking switch and requests at once (harness spec 3.2.0).

The pure rules are the spec's own conformance cases (`levels.json`, run in
`test_harness_spec.py`); these hold the server around them: what a chat's
request carries, how a level is stored and read, what the composer draws for
each control, the model page, `/v1/models` and `/api/v2/models`.
"""

from __future__ import annotations

from sqlalchemy import select

from lembas.db.models import Chat, Connection, Model, User
from lembas.security import api_tokens
from lembas.services import chat as chat_service
from lembas.services import model_prefs


def _model(db, model_id="m", **fields) -> Model:
    connection = db.scalar(select(Connection))
    if connection is None:
        connection = Connection(name="c", base_url="http://127.0.0.1:1", api_key_encrypted="")
        db.add(connection)
        db.commit()
    values = {"capabilities_json": {"reasoning": True}, **fields}
    model = Model(connection_id=connection.id, model_id=model_id, **values)
    db.add(model)
    db.commit()
    return model


def _chat(db, model: Model, level=...) -> Chat:
    user = db.scalars(select(User)).first()
    chat = Chat(
        user_id=user.id,
        model_id=model.model_id,
        connection_id=model.connection_id,
        params_json={} if level is ... else {"reasoning_effort": level},
    )
    db.add(chat)
    db.commit()
    return chat


def _qwen(db, model_id="qwen") -> Model:
    """The live shape: thinking can be switched off, one effort."""
    return _model(db, model_id, reasoning_efforts=["medium"], thinking_switch=True)


# --- What a chat's request carries --------------------------------------------------------
def test_off_sends_enable_thinking_false_and_no_effort(client, db, registered):
    body = chat_service.build_request(db, _chat(db, _qwen(db), "off"))

    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert "reasoning_effort" not in body


def test_on_sends_the_effort_and_no_enable_thinking(client, db, registered):
    body = chat_service.build_request(db, _chat(db, _qwen(db), "medium"))

    assert body["reasoning_effort"] == "medium"
    assert body["chat_template_kwargs"] == {"reasoning_effort": "medium"}


def test_a_chat_from_before_the_levels_reads_as_the_start_level(client, db, registered):
    """A cleared effort (`None`) used to mean "send nothing", which on these
    models is their server's default effort -- thinking, not off. It reads as
    the start level now, which is what the model was doing anyway."""
    model = _qwen(db)
    for stored in (None, ...):
        body = chat_service.build_request(db, _chat(db, model, stored))
        assert body["reasoning_effort"] == "medium"


def test_off_on_a_model_without_the_switch_is_its_start_level(client, db, registered):
    model = _model(db, reasoning_efforts=["low", "high"])

    body = chat_service.build_request(db, _chat(db, model, "off"))

    assert body["reasoning_effort"] == "low"
    assert "enable_thinking" not in body.get("chat_template_kwargs", {})


def test_a_model_not_marked_reasoning_is_sent_no_effort(client, db, registered):
    model = _model(db, capabilities_json={"reasoning": False})

    body = chat_service.build_request(db, _chat(db, model, "high"))

    assert "reasoning_effort" not in body and "chat_template_kwargs" not in body


def test_the_switch_on_a_model_with_no_efforts_is_off_or_nothing(client, db, registered):
    model = _model(db, capabilities_json={"reasoning": False}, thinking_switch=True)

    assert chat_service.levels_for(model).offered == ("off", "default")
    on = chat_service.build_request(db, _chat(db, model, "default"))
    assert "reasoning_effort" not in on and "chat_template_kwargs" not in on
    off = chat_service.build_request(db, _chat(db, model, "off"))
    assert off["chat_template_kwargs"] == {"enable_thinking": False}


def test_a_refused_effort_is_retried_without_enable_thinking_too():
    """quirks.json `effort-refused`, 3.2.0: the retry is the model's own default."""
    import asyncio

    from lembas.services import generation
    from lembas.services.llm.openai_client import Endpoint, LLMError

    sent: list[dict] = []

    async def fake_stream(endpoint, payload, **_):
        sent.append(payload)
        if len(sent) == 1:
            raise LLMError("Unexpected reasoning effort high. Supported types are low, medium")
        yield {"choices": []}

    original = generation.stream_chat
    generation.stream_chat = fake_stream
    try:
        body = {
            "model": "nowhere",
            "reasoning_effort": "high",
            "chat_template_kwargs": {"reasoning_effort": "high", "enable_thinking": True},
        }

        async def run():
            endpoint = Endpoint(base_url="http://127.0.0.1:1", api_key="", extra_headers={})
            return [
                c async for c in generation._stream_once(endpoint, body, None, "nowhere")
            ]

        asyncio.run(run())
    finally:
        generation.stream_chat = original
    assert "reasoning_effort" not in sent[1] and "chat_template_kwargs" not in sent[1]


# --- Storing and reading a level -------------------------------------------------------------
def test_a_new_chat_is_seeded_with_the_start_level(client, db, registered):
    _qwen(db)
    response = client.post("/api/chats/start", data={"content": "hi", "model_id": "qwen"})
    assert response.status_code in (200, 204, 303), response.text
    chat = db.scalars(select(Chat)).first()
    assert chat.params_json["reasoning_effort"] == "medium"


def test_off_chosen_before_the_chat_exists_is_stored(client, db, registered):
    _qwen(db)
    client.post(
        "/api/chats/start",
        data={"content": "hi", "model_id": "qwen", "reasoning_effort": "off"},
    )
    chat = db.scalars(select(Chat)).first()
    assert chat.params_json["reasoning_effort"] == "off"
    assert chat_service.build_request(db, chat)["chat_template_kwargs"] == {
        "enable_thinking": False
    }


def test_patching_off_stores_off(client, db, registered):
    chat = _chat(db, _qwen(db), "medium")

    assert client.patch(
        f"/api/chats/{chat.id}", data={"reasoning_effort": "off"}
    ).status_code == 204

    db.refresh(chat)
    assert chat.params_json["reasoning_effort"] == "off"


def test_switching_model_reseeds_a_level_the_new_one_does_not_offer(client, db, registered):
    """Off on a model with the switch, then a model without one: its start."""
    chat = _chat(db, _qwen(db), "off")
    _model(db, "plain", reasoning_efforts=["low", "high"])

    response = client.patch(f"/api/chats/{chat.id}", data={"model_id": "plain"})

    db.refresh(chat)
    assert chat.params_json["reasoning_effort"] == "low"
    # The composer's menu comes back out of band, drawn for the new model.
    assert response.status_code == 200
    assert 'id="chatbar-settings"' in response.text and "hx-swap-oob" in response.text
    assert "data-effort-range" in response.text and "data-thinking-switch" not in response.text


def test_switching_model_keeps_a_level_the_new_one_offers(client, db, registered):
    chat = _chat(db, _qwen(db), "off")
    _qwen(db, "qwen-two")

    client.patch(f"/api/chats/{chat.id}", data={"model_id": "qwen-two"})

    db.refresh(chat)
    assert chat.params_json["reasoning_effort"] == "off"


def test_a_persons_default_follows_the_levels(client, db, registered):
    qwen = _qwen(db)
    plain = _model(db, "plain", reasoning_efforts=["medium"])
    user = db.scalars(select(User)).first()

    saved = model_prefs.save(
        db, user, qwen, tools_off_now=set(), offered_gates=set(), effort="off"
    )
    assert saved is not None and model_prefs.default_effort(db, user, qwen) == "off"
    # One level is no choice: nothing to store, the start level is the default.
    assert model_prefs.save(
        db, user, plain, tools_off_now=set(), offered_gates=set(), effort="off"
    ) is None
    assert model_prefs.default_effort(db, user, plain) == "medium"


# --- The composer -----------------------------------------------------------------------------
def _composer(client, chat) -> str:
    response = client.get(f"/chat/{chat.id}")
    assert response.status_code == 200
    html = response.text
    start = html.index('id="chatbar-settings"')
    return html[start : start + 6000]


def test_two_levels_one_of_them_off_is_a_thinking_switch(client, db, registered):
    html = _composer(client, _chat(db, _qwen(db), "off"))

    assert 'role="switch"' in html and 'type="checkbox"' in html
    assert "data-effort-range" not in html
    # Off: the switch is drawn unticked, and the state select still holds it.
    at = html.index("data-thinking-switch")
    switch = html[at - 200 : at + 200]
    assert "checked" not in switch
    assert '<option value="off" selected' in html


def test_more_levels_are_the_slider(client, db, registered):
    model = _model(db, reasoning_efforts=["low", "medium", "high"], thinking_switch=True)
    html = _composer(client, _chat(db, model, "high"))

    assert "data-effort-range" in html and "data-thinking-switch" not in html
    assert '["off", "low", "medium", "high"]' in html


def test_one_level_and_no_team_is_no_menu_and_the_panel_has_its_own_button(
    client, db, registered
):
    """Nothing to set while writing: no Effort and Subagents button at all. The
    chat's settings panel (tools, skills, prompt, sampling) is the toolbar's
    own button, there either way."""
    model = _model(db, reasoning_efforts=["medium"])
    html = _composer(client, _chat(db, model))

    assert "data-picker-toggle" not in html.split("</div>", 1)[0]
    assert "data-effort" not in html.split("</div>", 1)[0]
    assert 'data-toggle="#chat-settings"' in html


def test_the_new_chat_page_draws_the_switch(client, db, registered):
    _qwen(db)
    html = client.get("/chat?model=qwen").text
    start = html.index('id="chatbar-settings"')

    assert 'role="switch"' in html[start : start + 6000]


# --- The model page ---------------------------------------------------------------------------
def _form(model: Model, **fields) -> dict:
    return {
        "display_name": "", "enabled": "true", "public": "true",
        "cap_reasoning": "yes", "reasoning_efforts": ["medium"], **fields,
    }


def test_the_model_page_saves_the_switch_and_requests_at_once(client, db, registered):
    model = _model(db)

    response = client.post(
        f"/admin/models/{model.id}",
        data=_form(model, thinking_switch="true", parallel="2", default_effort="off"),
    )
    assert response.status_code in (200, 303), response.text

    db.refresh(model)
    assert model.thinking_switch is True and model.parallel == 2
    assert model.single_session is False
    assert model.params_json.get("reasoning_effort") == "off"
    page = client.get(f"/admin/models/{model.id}/edit").text
    assert 'name="thinking_switch"' in page and 'name="parallel"' in page


def test_one_request_at_once_is_single_session(client, db, registered):
    model = _model(db)

    client.post(f"/admin/models/{model.id}", data=_form(model, parallel="1"))
    db.refresh(model)
    assert model.parallel == 1 and model.single_session is True

    client.post(f"/admin/models/{model.id}", data=_form(model, parallel=""))
    db.refresh(model)
    assert model.parallel is None and model.single_session is False


def test_a_default_of_off_without_the_switch_is_not_kept(client, db, registered):
    model = _model(db)

    client.post(f"/admin/models/{model.id}", data=_form(model, default_effort="off"))

    db.refresh(model)
    assert "reasoning_effort" not in (model.params_json or {})


# --- What clients are told --------------------------------------------------------------------
def _bearer(db, scopes=("app",)) -> dict[str, str]:
    user = db.scalars(select(User)).first()
    _, raw = api_tokens.create(db, user, name="test", scopes=list(scopes))
    return {"Authorization": f"Bearer {raw}"}


def test_v1_models_reports_levels_switch_and_parallel(client, db, registered):
    _model(db, "qwen", reasoning_efforts=["medium"], thinking_switch=True, parallel=2)
    _model(db, "solo", reasoning_efforts=["low", "high"], single_session=True)

    entries = {
        e["id"].split("/")[-1]: e
        for e in client.get("/v1/models", headers=_bearer(db, ("models",))).json()["data"]
    }

    qwen = entries["qwen"]
    assert qwen["thinking_switch"] is True and qwen["levels"] == ["off", "medium"]
    assert qwen["effort"] == "medium" and qwen["capacity"] == {"parallel": 2}
    solo = entries["solo"]
    assert "thinking_switch" not in solo and solo["levels"] == ["low", "high"]
    assert solo["capacity"] == {"parallel": 1, "single_session": True}


# --- Reasoning goes back during its turn (context.md §8) ------------------------------------
def test_a_tool_rounds_turn_carries_its_reasoning():
    from lembas.services import tools

    call = {"id": "c1", "name": "read", "arguments": "{}"}
    assert tools.assistant_turn([call], "", None, "Look at the parser.")[
        "reasoning_content"
    ] == "Look at the parser."
    assert "reasoning_content" not in tools.assistant_turn([call], "", None, "")


def test_a_continued_reply_sends_its_reasoning_back_and_earlier_turns_do_not(
    client, db, registered
):
    from lembas.db.models import ROLE_ASSISTANT, ROLE_USER

    chat = _chat(db, _qwen(db), "medium")
    first = chat_service.create_message(db, chat, ROLE_USER, "One")
    reply = chat_service.create_message(db, chat, ROLE_ASSISTANT, "Done.")
    reply.reasoning = "Easy."
    second = chat_service.create_message(db, chat, ROLE_USER, "Two")
    cut = chat_service.create_message(db, chat, ROLE_ASSISTANT, "Half")
    cut.reasoning = "Working on two."
    db.commit()
    assert first and second

    messages = chat_service.build_messages(db, chat)

    replies = [m for m in messages if m["role"] == "assistant"]
    assert "reasoning_content" not in replies[0]
    assert replies[1]["reasoning_content"] == "Working on two."


def test_a_title_on_a_model_with_the_switch_is_asked_with_thinking_off(mock_http):
    import asyncio
    import json

    import httpx

    from lembas.services.llm.openai_client import Endpoint

    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": "Bread"}}]}
        )

    mock_http(handler)
    title = asyncio.run(
        chat_service.generate_title(
            Endpoint(base_url="http://127.0.0.1:1", api_key="", extra_headers={}), "qwen", "Q", "A",
            template="Name it: {{question}}", thinking_off=True,
        )
    )
    assert title == "Bread"
    assert seen[0]["chat_template_kwargs"] == {"enable_thinking": False}


# --- No absolute paths in the prompt (context.md §11) ---------------------------------------
def test_a_chats_prompt_names_no_path_of_the_machine(client, db, registered):
    from lembas.config import settings
    from lembas.db.models import ROLE_USER

    chat = _chat(db, _qwen(db), "medium")
    chat_service.create_message(db, chat, ROLE_USER, "Hello")
    from lembas.services import tools as tools_service

    user = db.scalars(select(User)).first()
    toolset = tools_service.resolve_tools(db, chat, user).defs
    body = chat_service.build_request(
        db, chat, tools=[t.schema for t in toolset] if toolset else None
    )

    text = "\n".join(str(m.get("content")) for m in body["messages"])
    assert str(settings.data_dir) not in text
