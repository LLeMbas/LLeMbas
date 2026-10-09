"""Reasoning effort: what goes out, and what does not.

The second half matters as much as the first. There is no field that works
everywhere -- OpenAI and vLLM read `reasoning_effort`, llama.cpp drops it
silently and reads only `chat_template_kwargs` -- so both are sent. That is only
safe because neither is sent at all until somebody chooses an effort, which is
what keeps an endpoint strict about unknown parameters working exactly as it
did.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import Chat, Connection, Model, User
from lembas.services import chat as chat_service

from .conftest import control_named


def _model(db, **capabilities) -> Model:
    connection = Connection(name="c", base_url="http://127.0.0.1:1", api_key_encrypted="")
    db.add(connection)
    db.commit()
    model = Model(
        connection_id=connection.id,
        model_id="m",
        capabilities_json={"reasoning": True, **capabilities},
    )
    db.add(model)
    db.commit()
    return model


def _chat(db, effort: str | None = None) -> Chat:
    model = _model(db)
    user = db.scalars(select(User)).first()
    chat = Chat(
        user_id=user.id,
        model_id=model.model_id,
        connection_id=model.connection_id,
        params_json={"reasoning_effort": effort} if effort is not None else {},
    )
    db.add(chat)
    db.commit()
    return chat


# --- What reaches the endpoint -----------------------------------------------
def test_an_effort_goes_out_in_both_forms(client: TestClient, db, registered):
    """One value, two fields. Neither endpoint family reads the other's."""
    chat = _chat(db, "high")

    body = chat_service.build_request(db, chat)

    assert body["reasoning_effort"] == "high"
    assert body["chat_template_kwargs"] == {"reasoning_effort": "high"}


def test_no_effort_means_neither_field(client: TestClient, db, registered):
    """The whole safety of sending both. A chat nobody has set an effort on is
    byte-for-byte the request it was before this existed, so a provider that
    refuses unknown parameters is untouched until somebody opts in."""
    chat = _chat(db)

    body = chat_service.build_request(db, chat)

    assert "reasoning_effort" not in body
    assert "chat_template_kwargs" not in body


def test_a_cleared_effort_means_neither_field(client: TestClient, db, registered):
    """Cleared is stored as None, like every other parameter here."""
    chat = _chat(db, None)

    body = chat_service.build_request(db, chat)

    assert "reasoning_effort" not in body


@pytest.mark.parametrize("junk", ["sudo", "HIGH ", "maximum", "1"])
def test_a_value_that_is_not_an_effort_is_not_sent(client: TestClient, db, registered, junk):
    """Never trusted from the row: it could predate a change to the list."""
    chat = _chat(db, junk)

    assert "reasoning_effort" not in chat_service.build_request(db, chat)


def test_existing_chat_template_kwargs_are_kept(client: TestClient, db, registered):
    """Merged rather than replaced, so a future caller setting something else
    there does not lose it."""
    body: dict = {"chat_template_kwargs": {"enable_thinking": True}}

    chat_service.apply_effort(body, "low")

    assert body["chat_template_kwargs"] == {"enable_thinking": True, "reasoning_effort": "low"}


# --- Setting it ---------------------------------------------------------------
def test_patching_the_effort_stores_it(client: TestClient, db, registered):
    chat = _chat(db)

    assert client.patch(
        f"/api/chats/{chat.id}", data={"reasoning_effort": "medium"}
    ).status_code == 204

    db.refresh(chat)
    assert chat.params_json["reasoning_effort"] == "medium"


def test_an_empty_effort_clears_it(client: TestClient, db, registered):
    chat = _chat(db, "high")

    client.patch(f"/api/chats/{chat.id}", data={"reasoning_effort": ""})

    db.refresh(chat)
    assert chat.params_json["reasoning_effort"] is None


def test_an_unknown_effort_leaves_the_old_one(client: TestClient, db, registered):
    """Ignored, not refused: a typo should not cost the setting you had."""
    chat = _chat(db, "low")

    client.patch(f"/api/chats/{chat.id}", data={"reasoning_effort": "extreme"})

    db.refresh(chat)
    assert chat.params_json["reasoning_effort"] == "low"


def test_the_control_only_appears_on_a_reasoning_model(client: TestClient, db, registered):
    """The flag has existed with no reader since the beginning; this is its
    first job. Offering the control everywhere would offer a setting that does
    nothing almost everywhere."""
    chat = _chat(db)
    assert "data-effort" in client.get(f"/chat/{chat.id}").text

    model = db.scalars(select(Model)).one()
    model.capabilities_json = {"reasoning": False}
    db.commit()

    assert "data-effort" not in client.get(f"/chat/{chat.id}").text


# --- The per-model default ----------------------------------------------------
def test_a_new_chat_starts_from_the_models_defaults(client: TestClient, db, registered):
    """`Model.params_json` has claimed to do this since it was added and did it
    nowhere. It is empty on every existing row, so honouring it changes nothing
    until an administrator sets something."""
    model = _model(db)
    model.params_json = {"reasoning_effort": "high"}
    db.commit()

    client.post("/api/chats/start", data={"content": "hello", "model_id": "m"})

    chat = db.scalars(select(Chat)).one()
    assert chat.params_json["reasoning_effort"] == "high"


def test_a_model_with_no_defaults_starts_a_plain_chat(client: TestClient, db, registered):
    _model(db)

    client.post("/api/chats/start", data={"content": "hello", "model_id": "m"})

    assert db.scalars(select(Chat)).one().params_json == {}


# --- Choosable before the first prompt ---------------------------------------
def test_the_effort_select_carries_its_own_verb(client: TestClient, db, registered):
    """The same invariant the mode select needs, for the same reason.

    Both were built on `form="…"` pointing at an empty sibling form holding the
    `hx-patch`, and both therefore wrote nothing at all: `form=` scopes the
    values a request carries, it does not route the event that starts one.
    """
    chat = _chat(db)

    select = control_named(client.get(f"/chat/{chat.id}").text, "reasoning_effort")
    assert select["hx-patch"] == f"/api/chats/{chat.id}"
    assert select["form"] == "chat-params-form"


def test_the_effort_is_offered_before_there_is_a_chat(client: TestClient, db, registered):
    """Otherwise it is a setting you can only reach once it is too late to use.

    On the new-chat screen there is nothing to PATCH, so it is an ordinary field
    of the composer's form and carries no verb -- `_new_chat` reads it.
    """
    _model(db)

    select = control_named(client.get("/chat").text, "reasoning_effort")
    assert "hx-patch" not in select
    assert "form" not in select


def test_starting_a_chat_with_an_effort_stores_it(client: TestClient, db, registered):
    _model(db)

    client.post(
        "/api/chats/start",
        data={"content": "hello", "model_id": "m", "reasoning_effort": "low"},
    )

    assert db.scalars(select(Chat)).one().params_json["reasoning_effort"] == "low"


def test_an_explicit_effort_beats_the_models_default(client: TestClient, db, registered):
    """An inherited value is a starting point, not a ceiling."""
    model = _model(db)
    model.params_json = {"reasoning_effort": "high"}
    db.commit()

    client.post(
        "/api/chats/start",
        data={"content": "hello", "model_id": "m", "reasoning_effort": "low"},
    )

    assert db.scalars(select(Chat)).one().params_json["reasoning_effort"] == "low"


def test_a_nonsense_effort_at_the_start_falls_back(client: TestClient, db, registered):
    model = _model(db)
    model.params_json = {"reasoning_effort": "high"}
    db.commit()

    client.post(
        "/api/chats/start",
        data={"content": "hello", "model_id": "m", "reasoning_effort": "extreme"},
    )

    assert db.scalars(select(Chat)).one().params_json["reasoning_effort"] == "high"


# --- What the picker says is what is sent ---------------------------------------
def test_the_resolver_is_what_the_request_carries(client: TestClient, db, registered):
    """One resolver, so the control and the request cannot disagree. That
    disagreement is the whole reason the picker said "default": it named no
    level, and was true of nothing in particular."""
    chat = _chat(db, "high")

    assert chat_service.resolved_effort(chat) == "high"
    assert chat_service.build_request(db, chat)["reasoning_effort"] == "high"


def test_a_cleared_effort_is_not_resurrected_by_the_models_default(
    client: TestClient, db, registered
):
    """What the no-fallback decision buys. If `build_request` fell back to the
    model, `update_chat` storing None for a cleared effort would be undone
    underneath it and the off option would silently do nothing."""
    model = _model(db)
    model.params_json = {"reasoning_effort": "high"}
    user = db.scalars(select(User)).first()
    chat = Chat(
        user_id=user.id,
        model_id=model.model_id,
        connection_id=model.connection_id,
        params_json={"reasoning_effort": None},
    )
    db.add(chat)
    db.commit()

    assert chat_service.resolved_effort(chat) == ""
    body = chat_service.build_request(db, chat)
    assert "reasoning_effort" not in body
    assert "chat_template_kwargs" not in body


def test_choosing_off_before_the_chat_exists_sends_nothing(
    client: TestClient, db, registered
):
    """The one that would otherwise ship broken. `start_chat` declares
    `Form("")`, so an absent field and an empty one are the same thing there --
    with `value=""` on the off option the reader picks off, the value falls out
    of EFFORTS, and the model's default seeded onto the row stays. They get
    "high"."""
    model = _model(db)
    model.params_json = {"reasoning_effort": "high"}
    db.commit()

    client.post(
        "/api/chats/start",
        data={"content": "hello", "model_id": "m", "reasoning_effort": "off"},
    )
    chat = db.scalars(select(Chat)).first()

    assert not (chat.params_json or {}).get("reasoning_effort")
    assert "reasoning_effort" not in chat_service.build_request(db, chat)


def test_patching_off_clears_it(client: TestClient, db, registered):
    chat = _chat(db, "high")
    client.patch(f"/api/chats/{chat.id}", data={"reasoning_effort": "off"})

    db.expire_all()
    assert chat_service.resolved_effort(db.get(Chat, chat.id)) == ""


def test_switching_model_seeds_an_effort_that_was_never_chosen(
    client: TestClient, db, registered
):
    """So "what the picker shows is what is sent" stays true after a switch."""
    chat = _chat(db)
    second = Model(
        connection_id=chat.connection_id,
        model_id="m2",
        capabilities_json={"reasoning": True},
        params_json={"reasoning_effort": "medium"},
    )
    db.add(second)
    db.commit()

    client.patch(f"/api/chats/{chat.id}", data={"model_id": "m2"})

    db.expire_all()
    assert chat_service.resolved_effort(db.get(Chat, chat.id)) == "medium"


def test_switching_model_does_not_overwrite_a_chosen_effort(
    client: TestClient, db, registered
):
    chat = _chat(db, "low")
    second = Model(
        connection_id=chat.connection_id,
        model_id="m2",
        capabilities_json={"reasoning": True},
        params_json={"reasoning_effort": "high"},
    )
    db.add(second)
    db.commit()

    client.patch(f"/api/chats/{chat.id}", data={"model_id": "m2"})

    db.expire_all()
    assert chat_service.resolved_effort(db.get(Chat, chat.id)) == "low"


def test_switching_model_does_not_resurrect_a_cleared_effort(
    client: TestClient, db, registered
):
    """`None` means somebody cleared it deliberately. Only an ABSENT key is
    seeded, or "off" would silently undo itself on the next model change."""
    chat = _chat(db, "high")
    client.patch(f"/api/chats/{chat.id}", data={"reasoning_effort": "off"})
    second = Model(
        connection_id=chat.connection_id,
        model_id="m2",
        capabilities_json={"reasoning": True},
        params_json={"reasoning_effort": "high"},
    )
    db.add(second)
    db.commit()

    client.patch(f"/api/chats/{chat.id}", data={"model_id": "m2"})

    db.expire_all()
    assert chat_service.resolved_effort(db.get(Chat, chat.id)) == ""


def test_the_picker_never_says_default(client: TestClient, db, registered):
    """The one markup assertion. It named no level and was true of nothing."""
    chat = _chat(db, "medium")
    html = client.get(f"/chat/{chat.id}").text

    assert "Effort: default" not in html
    assert '<option value="off" >off</option>' in html.replace("\n", "").replace("  ", "")
    assert '<option value="medium" selected>' in html.replace("\n", "").replace("  ", "")


# --- A vocabulary that is not the same for every model -----------------------
#
# Reported from a real instance, on a model called Bonsai:
#
#   Jinja Exception: Unexpected reasoning effort high. Supported types are
#   xhigh (default), medium, and low.
#
# `chat_template_kwargs.reasoning_effort` is rendered into the model's own chat
# template, and a template that does not know the value calls `raise_exception`
# rather than ignoring it -- so the whole reply died, from an option this
# application had drawn in a menu.
BONSAI_ERROR = (
    "Jinja Exception: Unexpected reasoning effort high. "
    "Supported types are xhigh (default), medium, and low."
)


class _FakeModel:
    def __init__(self, efforts=None):
        self.reasoning_efforts = efforts or []


def test_a_model_that_has_said_nothing_gets_the_common_three():
    from lembas.services import chat as chat_service

    assert chat_service.efforts_for(_FakeModel()) == ("low", "medium", "high")


def test_a_model_can_take_xhigh_and_not_high():
    from lembas.services import chat as chat_service

    bonsai = _FakeModel(["xhigh", "medium", "low"])
    assert chat_service.efforts_for(bonsai) == ("low", "medium", "xhigh")
    assert "high" not in chat_service.efforts_for(bonsai)


def test_an_effort_the_model_refuses_is_never_sent():
    """The check that stops the crash happening at all."""
    from lembas.services import chat as chat_service

    supported = chat_service.efforts_for(_FakeModel(["xhigh", "medium", "low"]))
    body: dict = {}
    chat_service.apply_effort(body, "high", supported)
    assert body == {}

    chat_service.apply_effort(body, "xhigh", supported)
    assert body["reasoning_effort"] == "xhigh"
    assert body["chat_template_kwargs"]["reasoning_effort"] == "xhigh"


def test_a_value_this_application_never_heard_of_cannot_reach_a_request():
    from lembas.services import chat as chat_service

    assert chat_service.efforts_for(_FakeModel(["ludicrous"])) == ("low", "medium", "high")


def test_the_refusal_is_recognised_and_the_supported_list_read_out_of_it():
    from lembas.services import generation

    assert generation._effort_was_refused(BONSAI_ERROR)
    assert generation._advertised_efforts(BONSAI_ERROR) == ["low", "medium", "xhigh"]


def test_the_rejected_value_is_not_collected_as_a_supported_one():
    """The message names the refused effort first and the supported ones after,
    so anything reading the whole string would learn `high` from a sentence
    saying `high` is the problem."""
    from lembas.services import generation

    assert "high" not in generation._advertised_efforts(BONSAI_ERROR)


def test_an_ordinary_failure_is_not_retried_as_an_effort_problem():
    """Retrying a genuine failure would hide it behind a second request."""
    from lembas.services import generation

    for message in (
        "Connection refused.",
        "The model is still loading.",
        "context length exceeded",
    ):
        assert not generation._effort_was_refused(message)


def test_a_model_with_no_advertisement_simply_loses_the_refused_value():
    from lembas.services import generation

    assert generation._advertised_efforts("Unexpected reasoning effort high.") == []


# --- Reading the answer instead of asking somebody to know it ----------------
#
# llama-server publishes the loaded model's Jinja chat template on /props, and
# that template is the thing that rejects an effort it does not know -- so the
# accepted set is written down in the one authoritative place.
BONSAI_TEMPLATE = (
    "{%- if reasoning_effort not in ('xhigh', 'medium', 'low') %}"
    "{{- raise_exception('Unexpected reasoning effort ' ~ reasoning_effort ~ "
    "'. Supported types are xhigh (default), medium, and low.') }}{%- endif %}"
)
GPT_OSS_TEMPLATE = (
    '{%- set valid_efforts = ["low", "medium", "high"] %}'
    "{%- if reasoning_effort not in valid_efforts %}"
    "{{ raise_exception('bad effort') }}{% endif %}"
)


def test_the_accepted_set_is_read_out_of_the_template():
    from lembas.services import chat as chat_service

    assert chat_service.efforts_from_chat_template(BONSAI_TEMPLATE) == [
        "low", "medium", "xhigh",
    ]


def test_a_template_that_keeps_its_list_in_a_variable_is_read_too():
    """gpt-oss names the list rather than inlining it, so nothing near the
    `reasoning_effort` mention spells the values out."""
    from lembas.services import chat as chat_service

    assert chat_service.efforts_from_chat_template(GPT_OSS_TEMPLATE) == [
        "low", "medium", "high",
    ]


def test_an_unrelated_list_is_not_mistaken_for_a_vocabulary():
    from lembas.services import chat as chat_service

    template = '{%- set roles = ["user", "assistant", "system"] %}{{ messages }}'
    assert chat_service.efforts_from_chat_template(template) == []


def test_a_single_mention_is_not_a_vocabulary():
    """`{%- set reasoning_effort = 'medium' %}` is a default, not a list, and
    reading it as one would leave a model offering exactly one level."""
    from lembas.services import chat as chat_service

    assert chat_service.efforts_from_chat_template("{%- set reasoning_effort = 'medium' %}") == []


def test_a_template_that_says_nothing_says_nothing():
    from lembas.services import chat as chat_service

    assert chat_service.efforts_from_chat_template("") == []
    assert chat_service.efforts_from_chat_template("{{ messages }}") == []


def test_props_lives_beside_the_openai_surface_not_inside_it():
    """`/props` is llama-server's own route, at the server root -- a base URL
    written as `.../v1` would otherwise ask for `/v1/props`, which is a 404."""
    from lembas.services.llm.openai_client import Endpoint

    endpoint = Endpoint(base_url="http://host:8080/v1", api_key="", extra_headers={})
    assert endpoint.root_url("props") == "http://host:8080/props"

    bare = Endpoint(base_url="http://host:8080", api_key="", extra_headers={})
    assert bare.root_url("props") == "http://host:8080/props"
    # And the OpenAI surface is unchanged by any of this.
    assert bare.url("chat/completions") == "http://host:8080/v1/chat/completions"


def test_detecting_from_the_endpoint_writes_the_list(client, db, registered, mock_http):
    """The whole path: a button, a GET to /props, the template parsed, the
    model's list written."""
    import httpx
    from sqlalchemy import select

    from lembas.db.models import Connection, Model

    connection = Connection(name="local", base_url="http://127.0.0.1:1", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="bonsai"))
    db.commit()
    model = db.scalar(select(Model).where(Model.model_id == "bonsai"))

    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, json={"chat_template": BONSAI_TEMPLATE})

    mock_http(handler)
    response = client.post(
        f"/admin/models/{model.id}/detect-efforts", follow_redirects=False
    )
    assert response.status_code == 303

    db.expire_all()
    assert db.get(Model, model.id).reasoning_efforts == ["low", "medium", "xhigh"]
    # At the server root, not under /v1.
    assert asked and asked[0].endswith("/props")


def test_an_endpoint_with_no_props_leaves_the_list_alone(client, db, registered, mock_http):
    """OpenAI and vLLM have no such route, and "this cannot tell us" must not
    be recorded as "this model accepts nothing"."""
    import httpx
    from sqlalchemy import select

    from lembas.db.models import Connection, Model

    connection = Connection(name="hosted", base_url="http://127.0.0.1:2", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="gpt-x", reasoning_efforts=["low", "high"]))
    db.commit()
    model = db.scalar(select(Model).where(Model.model_id == "gpt-x"))

    mock_http(lambda request: httpx.Response(404, json={"error": "not found"}))
    client.post(f"/admin/models/{model.id}/detect-efforts", follow_redirects=False)

    db.expire_all()
    assert db.get(Model, model.id).reasoning_efforts == ["low", "high"]


def test_a_new_chat_offers_the_models_own_efforts(client: TestClient, db, registered):
    """`/chat?model=` offered the generic three whatever the model took.

    On Bonsai (low, medium, xhigh, default xhigh) that drew `high`, which it
    rejects, and no `xhigh`, so the configured default was not an option and
    the picker fell through to "off". The chat created from that screen got
    `xhigh` anyway, so the control said one thing and the first reply did
    another. Reported from the live instance.
    """
    model = _model(db)
    model.model_id = "bonsai"
    model.reasoning_efforts = ["low", "medium", "xhigh"]
    model.params_json = {"reasoning_effort": "xhigh"}
    db.commit()

    html = client.get("/chat?model=bonsai").text.replace("\n", "").replace("  ", "")

    assert '<option value="xhigh" selected>' in html
    assert '<option value="high"' not in html
    assert '<option value="off" selected' not in html
