"""Alibaba Cloud Model Studio's two connection types, and the rule that came
with the second: a connection its provider sells for interactive use only
(`services/interactive.py`).

Nothing here calls the provider: every address is answered by `mock_http`, and
the shapes are the ones the provider's own reference documents.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from lembas.db.models import (
    KIND_TASK,
    ROLE_ASSISTANT,
    Chat,
    Connection,
    Message,
    Model,
    Schedule,
    ScheduleRun,
    User,
)
from lembas.security import api_tokens
from lembas.services import chat as chat_service
from lembas.services import compaction as compaction_service
from lembas.services import fallback as fallback_service
from lembas.services import generation as generation_service
from lembas.services import interactive, pricing, settings_store
from lembas.services import schedules as schedules_service
from lembas.services.crypto import encrypt
from lembas.services.index import pipeline
from lembas.services.llm import detect, providers
from lembas.services.llm.openai_client import Endpoint, LLMError, chunk_usage, stream_chat
from lembas.services.schedule import models as models_service
from tests import test_schedule_runs as runs_base
from tests.test_schedule_runs import T0, _runs, _sweep

script = runs_base.script

PLAN_URL = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
PAYG_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


def _owner(db) -> User:
    return db.scalars(select(User).order_by(User.created_at)).first()


def _connection(db, name: str, kind: str, url: str, key: str = "k") -> Connection:
    connection = Connection(
        name=name, base_url=url, api_key_encrypted=encrypt(key), provider_type=kind
    )
    db.add(connection)
    db.commit()
    return connection


def _model(db, connection, model_id: str, position: int = 0, **fields) -> Model:
    row = Model(connection_id=connection.id, model_id=model_id, position=position,
                capabilities_json={"tools": True}, **fields)
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def plan(db, registered):
    """One model on a Token Plan connection, and nothing else."""
    connection = _connection(db, "Plan", "alibaba_plan", PLAN_URL, "sk-sp-abc")
    return _model(db, connection, "mallorn-large", context_length=1000)


def _twin(db) -> Model:
    return _model(db, _connection(db, "Payg", "alibaba", PAYG_URL, "sk-abc"), "mallorn-large", 1)


def _other(db) -> Model:
    local = _connection(db, "Local", "openai_compatible", "http://127.0.0.1:1/v1", "")
    return _model(db, local, "local-small", 2)


def _chat(db, model: Model, **fields) -> Chat:
    chat = Chat(user_id=_owner(db).id, model_id=model.model_id,
                connection_id=model.connection_id, **fields)
    db.add(chat)
    db.commit()
    return chat


def _sse(text: str) -> str:
    frame = json.dumps({"choices": [{"delta": {"content": text}}]})
    return f"data: {frame}\n\ndata: [DONE]\n\n"


# --- The two types ----------------------------------------------------------------------------
def test_the_types_their_hosts_and_what_follows_from_each():
    payg, plan_type = providers.get("alibaba"), providers.get("alibaba_plan")
    assert (payg.protocol, plan_type.protocol) == ("openai", "openai")
    assert not payg.interactive_only and not payg.subscription
    assert plan_type.interactive_only and plan_type.subscription
    assert payg.balance == plan_type.balance == providers.BALANCE_NONE
    assert not payg.reports_cost and not plan_type.reports_cost
    for url, kind in (
        (PAYG_URL, "alibaba"),
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", "alibaba"),
        ("https://dashscope-us.aliyuncs.com/compatible-mode/v1", "alibaba"),
        ("https://cn-hongkong.dashscope.aliyuncs.com/compatible-mode/v1", "alibaba"),
        ("https://llm-abc.eu-central-1.maas.aliyuncs.com/compatible-mode/v1", "alibaba"),
        (PLAN_URL, "alibaba_plan"),
        ("https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic", "alibaba_plan"),
        ("https://coding-intl.dashscope.aliyuncs.com/v1", "alibaba_plan"),
    ):
        assert providers.by_host(url).id == kind, url
    # Not a lookalike, and not a workspace that merely starts like a plan.
    assert providers.by_host("https://token-plan.example.org/v1") is None
    assert providers.by_host("https://evildashscope.aliyuncs.com/v1") is None
    # Every offered address is on a host of its own type.
    for kind in (payg, plan_type):
        assert kind.default_base_url == kind.regions[0].base_url
        for region in kind.regions:
            assert providers.by_host(region.base_url.replace("{WorkspaceId}", "llm-x")) is kind


def test_interactive_only_is_the_type_or_a_plans_host_under_another_type():
    assert providers.interactive_only("alibaba_plan", "")
    assert not providers.interactive_only("alibaba", PAYG_URL)
    assert not providers.interactive_only("openai_compatible", "http://127.0.0.1:8080/v1")
    # The plan's Anthropic-shaped address, set up as an Anthropic connection.
    anthropic_shaped = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic"
    assert providers.interactive_only("anthropic", anthropic_shaped)
    assert providers.subscription("anthropic", anthropic_shaped)


def test_thinking_goes_to_the_top_level_and_an_effort_asks_for_it():
    off = providers.shape_request(
        "alibaba", {"model": "m", "chat_template_kwargs": {"enable_thinking": False}}
    )
    assert off == {"model": "m", "enable_thinking": False}
    on = providers.shape_request(
        "alibaba_plan", {"model": "m", "chat_template_kwargs": {"enable_thinking": True}}
    )
    assert on == {"model": "m", "enable_thinking": True}
    body = {"model": "m", "reasoning_effort": "high",
            "chat_template_kwargs": {"reasoning_effort": "high"}}
    effort = providers.shape_request("alibaba", body)
    assert effort == {"model": "m", "reasoning_effort": "high", "enable_thinking": True}
    assert "chat_template_kwargs" in body, "the caller's body is never changed"
    # Nothing asked: nothing sent, the model's own default.
    assert providers.shape_request("alibaba", {"model": "m"}) == {"model": "m"}


def test_a_model_with_the_switch_is_told_on_as_well_as_off(db, registered):
    payg = _connection(db, "Payg", "alibaba", PAYG_URL)
    switched = _model(db, payg, "mallorn-switch", thinking_switch=True)
    plain = _model(db, payg, "mallorn-plain", 1)
    local = _model(db, _connection(db, "Local", "openai_compatible", "http://x.test/v1"),
                   "mallorn-local", 2, thinking_switch=True)

    def sent(model, level):
        body: dict = {}
        chat_service.apply_level(body, model, level)
        return providers.shape_request(model.connection.provider_type, body)

    # Some of the provider's models think by default and some do not: "not
    # off" is said, where the administrator marked the model as switchable.
    assert sent(switched, "default") == {"enable_thinking": True}
    assert sent(switched, "off") == {"enable_thinking": False}
    assert sent(plain, "default") == {}
    # Elsewhere the default stays unsaid, as it always was.
    assert sent(local, "default") == {}


def test_usage_reads_cache_hits_and_cache_creation():
    usage = chunk_usage({"choices": [], "usage": {
        "prompt_tokens": 3000, "completion_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": 1200, "cache_creation_input_tokens": 300},
    }})
    assert (usage["cached_tokens"], usage["cache_write_tokens"]) == (1200, 300)
    # The newest models say it twice; it is one figure.
    both = {"prompt_tokens_details": {"cache_write_tokens": 300,
                                      "cache_creation_input_tokens": 300}}
    assert providers.cache_write_tokens(both) == 300
    assert providers.cache_write_tokens({"prompt_tokens": 5}) is None
    # Priced as the price model has it: hits at `cached_input`, creation at
    # 125 % of input.
    price = {"input": 2.0, "cached_input": 0.4, "output": 8.0}
    assert pricing.cost_micro(
        price, prompt_tokens=3000, cached_tokens=1200, cache_write_tokens=300,
        completion_tokens=20,
    ) == 1500 * 2 + 1200 * 0.4 + 300 * 2 * 1.25 + 20 * 8


def test_a_key_of_the_other_product_is_noticed():
    assert providers.key_mismatch("alibaba_plan", "sk-abc")
    assert not providers.key_mismatch("alibaba_plan", "sk-sp-abc")
    assert providers.key_mismatch("alibaba", "sk-sp-abc")
    assert not providers.key_mismatch("alibaba", "sk-ws-abc")
    assert not providers.key_mismatch("alibaba", "")
    assert not providers.key_mismatch("openai", "sk-sp-abc")


async def test_detection_keeps_the_address_that_was_pasted(mock_http):
    def never(request: httpx.Request) -> httpx.Response:
        raise AssertionError("a known host is not asked anything")

    mock_http(never)
    workspace = "https://llm-abc.ap-northeast-1.maas.aliyuncs.com/compatible-mode/v1/"
    found = await detect.detect(workspace)
    assert (found["provider_type"], found["base_url"]) == ("alibaba", workspace.rstrip("/"))
    bare = await detect.detect("https://dashscope-intl.aliyuncs.com")
    assert bare["base_url"] == PAYG_URL
    plan_found = await detect.detect("https://token-plan.ap-southeast-1.maas.aliyuncs.com")
    assert (plan_found["provider_type"], plan_found["base_url"]) == ("alibaba_plan", PLAN_URL)


# --- The plan's quota -------------------------------------------------------------------------
async def test_a_used_up_plan_says_so_and_a_rate_limit_stays_a_rate_limit(mock_http):
    quota = {"error": {"message": "Your token-plan quota has been exhausted.",
                       "code": "insufficient_quota", "type": "insufficient_quota"}}
    mock_http(lambda request: httpx.Response(429, json=quota))

    async def ask(kind: str, url: str) -> LLMError:
        endpoint = Endpoint(base_url=url, api_key="k", extra_headers={}, provider_type=kind)
        with pytest.raises(LLMError) as caught:
            async for _ in stream_chat(endpoint, {"model": "m", "messages": []}):
                pass
        return caught.value

    used_up = await ask("alibaba_plan", PLAN_URL)
    assert used_up.quota_exhausted and used_up.status_code == 429
    assert "quota is used up" in used_up.message and "Rate limited" not in used_up.message
    # Pay-as-you-go uses the same code for its ordinary limit: not this.
    limited = await ask("alibaba", PAYG_URL)
    assert not limited.quota_exhausted and limited.message.startswith("Rate limited")

    mock_http(lambda request: httpx.Response(
        429, json={"error": {"message": "Requests rate limit exceeded, please try again later."}}
    ))
    busy = await ask("alibaba_plan", PLAN_URL)
    assert not busy.quota_exhausted and busy.message.startswith("Rate limited")


# --- A subscription is not priced -------------------------------------------------------------
def test_a_plans_replies_are_counted_and_never_priced(db, plan):
    plan.pricing_json = {"input": 2.0, "output": 8.0}
    db.commit()
    usage = {"prompt_tokens": 1000, "completion_tokens": 100}
    assert pricing.subscription(plan) and pricing.free(plan)
    assert pricing.price_reply(db, plan, usage) is None
    twin = _twin(db)
    twin.pricing_json = {"input": 2.0, "output": 8.0}
    db.commit()
    assert pricing.price_reply(db, twin, usage)["cost_micro"] == 2800


def test_the_model_page_says_subscription_and_keeps_stored_prices(client, db, plan):
    plan.pricing_json = {"input": 2.0}
    db.commit()
    page = client.get(f"/admin/models/{plan.id}/edit")
    assert page.status_code == 200
    assert 'data-testid="price-subscription"' in page.text
    assert 'name="price_input"' not in page.text


# --- Rule 1: scheduled work -------------------------------------------------------------------
def test_a_schedule_cannot_be_given_an_interactive_only_model(db, plan):
    owner = _owner(db)
    assert models_service.offered_models(db, owner) == []
    with pytest.raises(schedules_service.ScheduleError) as refused:
        schedules_service.model_choice(db, owner, "mallorn-large")
    assert str(refused.value) == schedules_service.INTERACTIVE_ONLY
    # With the same id on a pay-as-you-go connection, the schedule gets that one.
    twin = _twin(db)
    assert schedules_service.model_choice(db, owner, "mallorn-large") == ("mallorn-large",
                                                                    twin.connection_id)
    assert [m.id for m in models_service.offered_models(db, owner)] == [twin.id]


def test_the_api_refuses_it_in_words_and_never_lists_it(client, db, plan):
    _other(db)
    user = _owner(db)
    _, raw = api_tokens.create(db, user, name="App", scopes=["app"])
    auth = {"Authorization": f"Bearer {raw}"}
    body = {"title": "News", "instruction": "Summarise the news.",
            "rule": {"at": {"times": ["07:30"]}}, "model": "mallorn-large"}
    refused = client.post("/api/v2/schedules", json=body, headers=auth)
    assert refused.status_code == 400
    assert "interactive use only" in refused.json()["detail"]
    listed = client.get("/api/v2/schedules/models", headers=auth).json()
    assert "mallorn-large" not in json.dumps(listed) and "local-small" in json.dumps(listed)
    # What the apps and the CLI read about the model itself.
    models = {m["id"]: m for m in client.get("/api/v2/models", headers=auth).json()["models"]}
    assert models["mallorn-large"]["interactive_only"] is True
    assert "interactive_only" not in models["local-small"], "sent only when true"
    _, key = api_tokens.create(db, user, name="CLI", scopes=["models"], kind="device",
                               device_host="workstation")
    served = client.get("/v1/models", headers={"Authorization": f"Bearer {key}"}).json()
    flags = {m["id"].split("/")[-1]: m.get("interactive_only") for m in served["data"]}
    assert flags["mallorn-large"] is True and flags["local-small"] is None


def test_the_schedule_form_does_not_offer_it(client, db, plan):
    _other(db)
    settings_store.update(db, {"enabled": True}, key=settings_store.SCHEDULES)
    page = client.get("/scheduled/new")
    assert page.status_code == 200
    assert "local-small" in page.text and "mallorn-large" not in page.text


@pytest.mark.anyio
async def test_a_run_whose_model_became_interactive_only_fails_and_says_so(db, script):
    payg = _connection(db, "Payg", "alibaba", PAYG_URL)
    _model(db, payg, "mallorn-large")
    _other(db)
    schedule = schedules_service.create(
        db, owner=_owner(db), title="Build check", instruction="Check the build.",
        rule={"start": T0.isoformat(), "every": {"hours": 1}}, model_id="mallorn-large",
    )
    schedule.next_fire_at = T0
    # The administrator moves the connection onto a plan.
    payg.provider_type, payg.base_url = "alibaba_plan", PLAN_URL
    db.commit()

    await _sweep(script, T0)
    (run,) = _runs(db, schedule)
    assert run.status == "failed" and "interactive use only" in run.error
    assert script.turns == [], "nothing was asked of any model, not even another one"
    assert not run.model_id


async def test_a_scheduled_reply_is_refused_before_anything_is_sent(db, plan, monkeypatch):
    asked = []

    async def stream(endpoint, payload, **_):
        asked.append(endpoint.base_url)
        yield {"choices": [{"delta": {"content": "Done."}}]}

    monkeypatch.setattr(generation_service, "stream_chat", stream)

    async def run_reply(chat: Chat, machine: bool = False) -> Message:
        # A run's turn is the one the runner writes: a `machine` turn.
        db.add(Message(chat_id=chat.id, role="user", content="Check the build.", complete=True,
                       machine=machine))
        reply = Message(chat_id=chat.id, role=ROLE_ASSISTANT, content="", complete=False)
        db.add(reply)
        db.commit()
        await generation_service._run(
            generation_service.Generation(chat_id=chat.id, message_id=reply.id)
        )
        db.expire_all()
        return db.get(Message, reply.id)

    # A run's own chat.
    task = await run_reply(_chat(db, plan, kind=KIND_TASK, unattended=True), machine=True)
    assert "interactive use only" in task.error and asked == []
    # A subagent under a run: the same run.
    parent = _chat(db, plan, kind=KIND_TASK, unattended=True)
    child = await run_reply(
        _chat(db, plan, parent_chat_id=parent.id, unattended=True), machine=True
    )
    assert "interactive use only" in child.error and asked == []
    # A person's chat, and a subagent they started in it: interactive.
    mine = _chat(db, plan, title="Named", title_generated=True)
    assert (await run_reply(mine)).error == ""
    helper = _chat(db, plan, parent_chat_id=mine.id, unattended=True, title_generated=True)
    assert (await run_reply(helper)).error == ""
    assert asked == [PLAN_URL, PLAN_URL]


def test_a_subagent_under_a_chat_a_schedule_is_continuing_is_the_runs(db, plan):
    mine = _chat(db, plan)
    child = _chat(db, plan, parent_chat_id=mine.id, unattended=True)
    assert not interactive.scheduled_work(db, mine)
    assert not interactive.scheduled_work(db, child)
    schedule = Schedule(user_id=mine.user_id, title="t", instruction="i", chat_id=mine.id)
    db.add(schedule)
    db.flush()
    db.add(ScheduleRun(schedule_id=schedule.id, user_id=mine.user_id, chat_id=mine.id,
                       status="running", due_at=datetime.now(UTC)))
    db.commit()
    assert interactive.scheduled_work(db, child)
    # The chat's own turns stay its person's: the run's reply is already on a
    # model the schedule may use.
    assert not interactive.scheduled_work(db, mine)


def test_a_scheduled_run_does_not_fall_back_onto_a_plan(db, plan):
    local = _other(db)
    local.fallback_model_ids = [plan.id]
    db.commit()
    owner = _owner(db)
    endpoint = Endpoint.from_connection(local.connection)
    person = fallback_service.plan(db, _chat(db, local), owner, local, endpoint, "local-small")
    assert [c.model_id for c in person.fallbacks] == ["mallorn-large"]
    run = fallback_service.plan(
        db, _chat(db, local, kind=KIND_TASK), owner, local, endpoint, "local-small"
    )
    assert run.fallbacks == []


# --- Rule 2: background calls on a chat's behalf ----------------------------------------------
def test_where_a_background_call_goes(db, plan):
    chat = _chat(db, plan)
    endpoint, model_id = chat_service.resolve_endpoint(db, chat)
    # Nothing else: nowhere.
    assert interactive.background(db, chat, endpoint, model_id) is None
    # The instance's default model, when it is not interactive-only either.
    other = _other(db)
    assert interactive.background(db, chat, endpoint, model_id) is None, "not just any model"
    settings_store.update(db, {"default_model": "local-small"})
    routed = interactive.background(db, chat, endpoint, model_id)
    assert (routed[0].base_url, routed[1]) == (other.connection.base_url, "local-small")
    # The same model on a pay-as-you-go connection comes first.
    _twin(db)
    routed = interactive.background(db, chat, endpoint, model_id)
    assert (routed[0].base_url, routed[1]) == (PAYG_URL, "mallorn-large")
    # A chat that is not on such a connection is left exactly as it is.
    plain = _chat(db, other)
    there = chat_service.resolve_endpoint(db, plain)
    assert interactive.background(db, plain, *there) == there


def test_a_default_model_that_is_interactive_only_is_not_where_it_goes(db, plan):
    second = _model(db, plan.connection, "mallorn-small", 1)
    settings_store.update(db, {"default_model": second.model_id})
    chat = _chat(db, plan)
    assert interactive.background(db, chat, *chat_service.resolve_endpoint(db, chat)) is None


async def test_the_title_is_asked_of_the_twin_or_not_asked_at_all(db, plan, monkeypatch):
    asked = []

    async def titled(endpoint, model_id, *args, **kwargs):
        asked.append((endpoint.base_url, model_id, kwargs.get("thinking_off")))
        return "From the model"

    monkeypatch.setattr(chat_service, "generate_title", titled)

    async def name(chat: Chat) -> str:
        endpoint, model_id = chat_service.resolve_endpoint(db, chat)
        job = {"endpoint": endpoint, "model_id": model_id, "question": "What is lembas?",
               "answer": "Waybread.", "template": "Name it.", "thinking_off": True}
        return await generation_service._title_later(chat.id, "What is lembas?", job)

    # No other connection: the first words stay, and no model is asked.
    assert await name(_chat(db, plan)) == "What is lembas?"
    assert asked == []
    _twin(db)
    assert await name(_chat(db, plan)) == "From the model"
    # Not asked to stop thinking: that mark was read from another row.
    assert asked == [(PAYG_URL, "mallorn-large", False)]


def _exchange(db, chat: Chat) -> Message:
    when = datetime.now(UTC) - timedelta(minutes=5)
    db.add(Message(chat_id=chat.id, role="user", content="what is lembas?", created_at=when))
    reply = Message(chat_id=chat.id, role="assistant", content="Waybread.",
                    created_at=when + timedelta(seconds=1), complete=True)
    db.add(reply)
    db.commit()
    return reply


async def test_compaction_goes_to_the_twin_or_is_refused_in_words(client, db, plan, mock_http):
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(200, text=_sse("## Summary\n\nAsking about lembas."))

    mock_http(handler)
    chat = _chat(db, plan)
    reply = _exchange(db, chat)

    # Nowhere to go: refused before anything is sent, and the button says why.
    with pytest.raises(LLMError) as refused:
        await compaction_service.summarise(chat.id, upto_id=reply.id)
    assert "interactive use only" in refused.value.message and hosts == []
    assert "interactive use only" in interactive.compact_refusal(db, chat)
    pressed = client.post(f"/api/chats/{chat.id}/compact")
    assert pressed.status_code == 502 and "interactive use only" in pressed.text
    assert hosts == []

    _twin(db)
    assert interactive.compact_refusal(db, chat) == ""
    summary = await compaction_service.summarise(chat.id, upto_id=reply.id)
    assert "Asking about lembas." in summary
    assert hosts == ["dashscope-intl.aliyuncs.com"]


async def test_a_subagents_own_compaction_is_judged_by_the_subagents_model(
    db, plan, mock_http, monkeypatch
):
    """`interactive.md` §3: a subagent is the person's, the summary of its
    conversation is not their turn. It goes by the subagent's model -- its
    twin, or skipped with the turn going on -- whatever the lead runs on."""
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(200, text=_sse("## Summary\n\nKneading."))

    mock_http(handler)
    monkeypatch.setattr(compaction_service, "should_compact", lambda *a, **k: True)
    lead = _chat(db, _other(db))
    child = _chat(db, plan, parent_chat_id=lead.id, unattended=True)
    _exchange(db, child)
    pending = Message(chat_id=child.id, role=ROLE_ASSISTANT, content="", complete=False)
    db.add(pending)
    db.commit()
    generation = generation_service.Generation(chat_id=child.id, message_id=pending.id)

    # Nowhere to go: skipped, nothing sent, and nothing raised -- the turn goes on.
    await generation_service._maybe_compact(generation)
    db.expire_all()
    assert hosts == [] and not db.get(Chat, child.id).compact_summary

    _twin(db)
    await generation_service._maybe_compact(generation)
    db.expire_all()
    assert hosts == ["dashscope-intl.aliyuncs.com"]
    assert "Kneading." in db.get(Chat, child.id).compact_summary


def test_the_index_is_not_built_on_an_interactive_only_connection(db, plan, monkeypatch):
    monkeypatch.setattr(pipeline.vec, "available", lambda: True)
    _model(db, plan.connection, "mallorn-embed", 1, kind="embedding")
    settings_store.update(
        db, {"embedding_model_id": "mallorn-embed"}, key=settings_store.EXTRACTION
    )
    assert pipeline.embedder(db) is None
    payg = _connection(db, "Payg", "alibaba", PAYG_URL)
    _model(db, payg, "mallorn-embed", 2, kind="embedding")
    found = pipeline.embedder(db)
    assert found is None or found.endpoint.base_url == PAYG_URL


# --- The administrator's form -----------------------------------------------------------------
def test_the_form_offers_regions_warns_and_says_why(client, db, plan):
    page = client.get("/admin/connections")
    assert page.status_code == 200
    for expected in (
        'data-testid="region-alibaba"', 'data-testid="region-alibaba_plan"',
        PAYG_URL, PLAN_URL, 'data-testid="key-mismatch"',
        'data-testid="interactive-only-note"',
        "forbid automated services, batch scripts and scheduled background jobs",
    ):
        assert expected in page.text, expected
    # A balance is not offered for either type: there is none to read.
    assert f'/admin/connections/{plan.connection_id}/balance' not in page.text


def test_a_region_pattern_is_not_saved_as_it_stands(client, db, registered):
    made = client.post("/admin/connections", data={
        "name": "Frankfurt", "provider": "ali", "provider_type": "alibaba",
        "base_url": "https://{WorkspaceId}.eu-central-1.maas.aliyuncs.com/compatible-mode/v1",
    }, follow_redirects=False)
    assert made.status_code == 400 and "WorkspaceId" in made.text
    assert db.scalar(select(Connection)) is None


def test_a_stored_key_of_the_other_product_is_flagged_without_being_sent(client, db, plan):
    plan.connection.api_key_encrypted = encrypt("sk-not-a-plan-key")
    db.commit()
    page = client.get("/admin/connections")
    assert "sk-not-a-plan-key" not in page.text
    # Shown from the first paint for the stored key, not only once typed.
    # Only while the saved type is still the one selected: the server judged
    # the stored key against that one.
    assert "x-show=\"wrongKey() || (key ===" in page.text and "&& type ===" in page.text


# --- After the review of 2.0.0-beta.13..HEAD ---------------------------------------------------
def test_a_personal_api_key_is_not_served_an_interactive_only_model(client, db, plan, mock_http):
    """A request to `/v1` does not say what it is for, so the token decides:
    a linked device's is a person at a terminal, any other is a script."""
    _other(db)
    user = _owner(db)
    sent: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.host)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    mock_http(upstream)
    _, personal = api_tokens.create(db, user, name="A script", scopes=["models"])
    _, device = api_tokens.create(db, user, name="CLI", scopes=["models"], kind="device",
                                  device_host="workstation")
    body = {"model": "plan/mallorn-large", "messages": [{"role": "user", "content": "hi"}]}

    def call(key: str, **extra):
        return client.post("/v1/chat/completions", json={**body, **extra},
                           headers={"Authorization": f"Bearer {key}"})

    refused = call(personal)
    assert refused.status_code == 403 and sent == []
    assert refused.json()["error"]["message"] == (
        "plan/mallorn-large is on a connection for interactive use only: its provider's "
        "terms do not allow scheduled or background work. Choose a model on a pay-as-you-go "
        "connection."
    )
    listed = client.get("/v1/models", headers={"Authorization": f"Bearer {personal}"}).json()
    assert [m["id"] for m in listed["data"] if m.get("kind") == "chat"] == ["local/local-small"]
    # Its other models are its own as before.
    assert call(personal, model="local/local-small").status_code == 200
    # The linked CLI is served, and trusted to follow the spec for the rest.
    assert call(device).status_code == 200
    assert sent == ["127.0.0.1", "token-plan.ap-southeast-1.maas.aliyuncs.com"]
    emb = client.post("/v1/embeddings", json={"model": "plan/mallorn-large", "input": "x"},
                      headers={"Authorization": f"Bearer {personal}"})
    assert emb.status_code in (400, 403)


def test_a_scheduled_runs_picture_is_not_reviewed_on_a_plan(db, plan):
    from types import SimpleNamespace

    from lembas.services.images import tool as image_tool

    plan.capabilities_json = {"vision": True, "tools": True}
    db.commit()
    run_chat = _chat(db, plan, kind=KIND_TASK, unattended=True)
    mine = _chat(db, plan)

    def reviewer(chat: Chat):
        return image_tool._reviewer(SimpleNamespace(
            image_config={"review_enabled": True, "review_model_id": "mallorn-large"},
            data_group="default", model_id=chat.model_id, connection_id=chat.connection_id,
            chat_id=chat.id,
        ))

    # A person's own reply: reviewed where the administrator said.
    assert reviewer(mine)[0].base_url == PLAN_URL
    # A run: nobody, rather than the plan --
    assert reviewer(run_chat) is None
    # -- or the same model on a connection a run may use.
    twin = _twin(db)
    twin.capabilities_json = {"vision": True}
    db.commit()
    assert reviewer(run_chat)[0].base_url == PAYG_URL
    assert reviewer(mine)[0].base_url == PLAN_URL


def test_a_person_writing_into_a_runs_chat_is_a_person(db, plan):
    """`interactive.md` §2. The run's own turn is the one the runner wrote
    (`machine`); what the person writes there afterwards is theirs."""
    chat = _chat(db, plan, kind=KIND_TASK, unattended=True)
    child = _chat(db, plan, parent_chat_id=chat.id, unattended=True)
    assert interactive.scheduled_work(db, chat), "a run's chat before anything is in it"
    when = datetime.now(UTC)
    db.add(Message(chat_id=chat.id, role="user", content="Check.", machine=True,
                   created_at=when))
    db.commit()
    assert interactive.scheduled_work(db, chat) and interactive.scheduled_work(db, child)
    db.add(Message(chat_id=chat.id, role="user", content="And what failed?",
                   created_at=when + timedelta(minutes=5)))
    db.commit()
    assert not interactive.scheduled_work(db, chat)
    assert not interactive.scheduled_work(db, child), "what their turn starts is theirs too"
    # A job's note arriving after that is the harness's again.
    db.add(Message(chat_id=chat.id, role="user", content="[job 1 ended]", machine=True,
                   created_at=when + timedelta(minutes=9)))
    db.commit()
    assert interactive.scheduled_work(db, chat)


async def test_a_routed_summary_is_shaped_counted_and_priced_on_the_model_asked(
    db, plan, mock_http
):
    from lembas.services import usage as usage_service

    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        usage = json.dumps({"choices": [], "usage": {"prompt_tokens": 1000,
                                                     "completion_tokens": 100}})
        frame = json.dumps({"choices": [{"delta": {"content": "Summary."}}]})
        return httpx.Response(200, text=f"data: {frame}\n\ndata: {usage}\n\ndata: [DONE]\n\n")

    mock_http(handler)
    plan.thinking_switch = True
    twin = _twin(db)
    twin.pricing_json = {"input": 2.0, "output": 8.0}
    db.commit()
    chat = _chat(db, plan, params_json={"reasoning_effort": "off"})
    reply = _exchange(db, chat)
    recorded: list[dict] = []
    real = usage_service.record

    def record(db_, user_id, **fields):
        recorded.append(fields)
        return real(db_, user_id, **fields)

    usage_service.record = record
    try:
        assert await compaction_service.summarise(chat.id, upto_id=reply.id)
    finally:
        usage_service.record = real
    # The twin has no thinking switch: it is not sent the plan model's "off".
    assert "enable_thinking" not in bodies[0] and "chat_template_kwargs" not in bodies[0]
    assert recorded[0]["model_id"] == "mallorn-large"
    assert recorded[0]["cost_micro"] == 1000 * 2 + 100 * 8, "priced by the twin's list"

    # A model whose window is below what must be sent is nowhere to go.
    twin.context_length = 5
    db.commit()
    with pytest.raises(LLMError) as refused:
        await compaction_service.summarise(chat.id, upto_id=reply.id)
    assert refused.value.interactive_only and len(bodies) == 1


async def test_a_skipped_automatic_compaction_is_said_once(db, plan, monkeypatch):
    monkeypatch.setattr(compaction_service, "should_compact", lambda *a, **k: True)
    generation_service._SAID_NO_COMPACT.clear()
    chat = _chat(db, plan)
    _exchange(db, chat)
    pending = Message(chat_id=chat.id, role=ROLE_ASSISTANT, content="", complete=False)
    db.add(pending)
    db.commit()
    first = generation_service.Generation(chat_id=chat.id, message_id=pending.id)
    await generation_service._maybe_compact(first)
    assert "were not summarised" in "".join(first.content)
    again = generation_service.Generation(chat_id=chat.id, message_id=pending.id)
    await generation_service._maybe_compact(again)
    assert again.content == []


async def test_an_interrupted_reply_on_a_plan_is_not_carried_on_by_itself(db, plan):
    chat = _chat(db, plan)
    local_chat = _chat(db, _other(db))
    rows = []
    for one in (chat, local_chat):
        db.add(Message(chat_id=one.id, role="user", content="Go on.", complete=True))
        row = Message(chat_id=one.id, role=ROLE_ASSISTANT, content="Half of", complete=True,
                      interrupted=True, model_id=one.model_id,
                      connection_id=one.connection_id)
        db.add(row)
        rows.append(row)
    db.commit()
    started = []
    real = generation_service.ensure
    generation_service.ensure = lambda *a, **k: started.append(a)
    try:
        assert await generation_service.resume_interrupted(delay=0) == 1
    finally:
        generation_service.ensure = real
    db.expire_all()
    assert [a[0] for a in started] == [local_chat.id]
    assert db.get(Message, rows[0].id).interrupted is True


def test_the_notes(db, plan, mock_http):
    import asyncio

    from lembas.services import model_state
    from lembas.services.llm import health
    from lembas.services.schedule import compile as compile_service

    # A host spelled to its root is the same host.
    assert providers.plan_host("https://token-plan.ap-southeast-1.maas.aliyuncs.com./v1")
    assert providers.by_host("https://dashscope-intl.aliyuncs.com./x").id == "alibaba"
    # A zero in one place does not hide the figure in another.
    assert providers.cache_write_tokens({
        "prompt_tokens_details": {"cache_write_tokens": 0, "cache_creation_input_tokens": 300}
    }) == 300
    assert providers.cache_write_tokens({"prompt_tokens_details": {"cache_write_tokens": 0}}) == 0
    # Words → a schedule is not asked of a plan when it is all there is.
    assert compile_service.endpoint_for(db, _owner(db)) is None
    # Nothing polls a plan by itself; an administrator's Test still does.
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.host)
        return httpx.Response(200, json={"data": [{"id": "mallorn-large"}]})

    mock_http(handler)
    health.reset()
    assert asyncio.run(health.tick(force=True)) == 0
    assert asyncio.run(model_state.states_for([plan])) == {}
    assert asked == []
    assert asyncio.run(health.check(db, plan.connection))["status"] == "ok"
    assert asked == ["token-plan.ap-southeast-1.maas.aliyuncs.com"]


async def test_a_quota_error_inside_a_stream_is_the_same_sentence(mock_http):
    frame = json.dumps({"error": {"message": "Your token-plan quota has been exhausted.",
                                  "code": "insufficient_quota"}})
    mock_http(lambda request: httpx.Response(200, text=f"data: {frame}\n\n"))
    endpoint = Endpoint(base_url=PLAN_URL, api_key="k", extra_headers={},
                        provider_type="alibaba_plan")
    with pytest.raises(LLMError) as caught:
        async for _ in stream_chat(endpoint, {"model": "m", "messages": []}):
            pass
    assert caught.value.quota_exhausted and "quota is used up" in caught.value.message


def test_the_providers_newer_name_is_the_same_plan_and_the_same_service():
    """`qwencloudapi.com` is the same provider: its Token Plan host (which has
    no region in it) is a plan whatever type it was saved under, and its
    pay-as-you-go host is the pay-as-you-go type."""
    plan = "https://token-plan.maas.qwencloudapi.com/compatible-mode/v1"
    assert providers.plan_host(plan)
    assert providers.plan_host("https://token-plan.maas.qwencloudapi.com/apps/anthropic")
    assert providers.by_host(plan).id == providers.ALIBABA_PLAN
    assert providers.interactive_only("openai_compatible", plan)
    payg = "https://ap-southeast-1.maas.qwencloudapi.com/compatible-mode/v1"
    assert not providers.plan_host(payg)
    assert providers.by_host(payg).id == providers.ALIBABA
    assert not providers.interactive_only(providers.ALIBABA, payg)
