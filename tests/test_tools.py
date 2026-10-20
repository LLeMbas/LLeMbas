"""Tool calling: reassembling calls, deciding what is offered, running it."""

from __future__ import annotations

import json

import pytest

from lembas.db.models import Chat, Connection, Model
from lembas.services import settings_store
from lembas.services import tools as tools_service
from lembas.services.llm.openai_client import delta_tool_calls, finish_reason
from lembas.services.search.base import SearchError, SearchResult


# --- Reading the stream ------------------------------------------------------
def test_delta_tool_calls_reads_the_normal_shape():
    chunk = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "a"}]}}]}
    assert delta_tool_calls(chunk) == [{"index": 0, "id": "a"}]


@pytest.mark.parametrize(
    "chunk", [{}, {"choices": []}, {"choices": [{}]}, {"choices": [{"delta": {}}]}]
)
def test_delta_tool_calls_tolerates_junk(chunk):
    assert delta_tool_calls(chunk) == []


def test_finish_reason_is_read_when_present():
    assert finish_reason({"choices": [{"finish_reason": "tool_calls"}]}) == "tool_calls"
    assert finish_reason({"choices": [{}]}) == ""


# --- The accumulator ---------------------------------------------------------
def test_arguments_split_across_chunks_are_rejoined():
    """Arguments arrive one token at a time; the id and name arrive once, on
    the first fragment only. This is the real shape llama.cpp produces."""
    accumulator = tools_service.ToolCallAccumulator()
    accumulator.feed(
        [{"index": 0, "id": "c1", "function": {"name": "web_search", "arguments": "{"}}]
    )
    for piece in ['"query"', ":", '"mallorn', ' tree"', "}"]:
        accumulator.feed([{"index": 0, "function": {"arguments": piece}}])

    assert accumulator.calls == [
        {"id": "c1", "name": "web_search", "arguments": '{"query":"mallorn tree"}'}
    ]


def test_two_calls_are_kept_apart_by_index():
    """Not by name: a model calling the same tool twice in one turn is exactly
    the case that breaks if name is the key."""
    accumulator = tools_service.ToolCallAccumulator()
    accumulator.feed(
        [
            {"index": 0, "id": "a", "function": {"name": "web_search", "arguments": '{"q":1}'}},
            {"index": 1, "id": "b", "function": {"name": "web_search", "arguments": '{"q":2}'}},
        ]
    )
    assert [c["id"] for c in accumulator.calls] == ["a", "b"]
    assert accumulator.calls[1]["arguments"] == '{"q":2}'


def test_a_missing_index_is_treated_as_the_only_call():
    """Some servers omit index entirely when there is just one call."""
    accumulator = tools_service.ToolCallAccumulator()
    accumulator.feed([{"function": {"name": "web_search", "arguments": "{}"}}])
    assert len(accumulator.calls) == 1


def test_a_call_with_no_name_is_not_a_call():
    accumulator = tools_service.ToolCallAccumulator()
    accumulator.feed([{"index": 0, "function": {"arguments": "{}"}}])
    assert accumulator.calls == []
    assert not accumulator


def test_an_id_is_invented_when_the_server_supplies_none():
    """The id is required when the results are sent back, and not every server
    provides one."""
    accumulator = tools_service.ToolCallAccumulator()
    accumulator.feed([{"index": 0, "function": {"name": "web_search", "arguments": "{}"}}])
    assert accumulator.calls[0]["id"]


# --- What gets offered -------------------------------------------------------
def _chat_with(db, user_id, *, capabilities):
    connection = Connection(name="c", base_url="http://127.0.0.1:1", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(
        Model(connection_id=connection.id, model_id="m", capabilities_json=capabilities)
    )
    db.commit()
    chat = Chat(user_id=user_id, model_id="m", connection_id=connection.id)
    db.add(chat)
    db.commit()
    return chat


def _user(db, user_id):
    from lembas.db.models import User

    return db.get(User, user_id)


def _names(offered):
    return {tool["function"]["name"] for tool in offered}


def test_web_search_is_absent_when_search_is_off(db, user_id):
    """The library tools do not depend on a search provider, so they stay."""
    chat = _chat_with(db, user_id, capabilities={"tools": True})
    offered = _names(tools_service.enabled_tools(db, chat, _user(db, user_id)))
    assert "web_search" not in offered
    assert "notes_search" in offered


def test_nothing_at_all_without_the_tools_capability(db, user_id):
    """Sending a tools array to an endpoint that does not implement tool calling
    fails the entire request, exactly as image parts do without vision."""
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    chat = _chat_with(db, user_id, capabilities={})
    assert tools_service.enabled_tools(db, chat, _user(db, user_id)) == []


def test_web_search_is_offered_when_everything_lines_up(db, user_id):
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    chat = _chat_with(db, user_id, capabilities={"tools": True})
    assert "web_search" in _names(tools_service.enabled_tools(db, chat, _user(db, user_id)))


def test_a_model_predating_the_split_keeps_its_tools(db, user_id):
    """Rows configured before the per-tool flags existed have no tool_* keys.
    Reading absent as off would silently take web search away from every model
    already set up for it."""
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    chat = _chat_with(db, user_id, capabilities={"tools": True})
    assert "web_search" in _names(tools_service.enabled_tools(db, chat, _user(db, user_id)))


def test_a_family_turned_off_for_the_model_is_withheld(db, user_id):
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    chat = _chat_with(
        db, user_id, capabilities={"tools": True, "tool_notes": False}
    )
    offered = _names(tools_service.enabled_tools(db, chat, _user(db, user_id)))
    assert "notes_search" not in offered
    assert "web_search" in offered, "turning one family off must not affect another"


def test_web_search_is_withheld_when_the_provider_cannot_run(db, user_id, monkeypatch):
    """Offering a tool that will fail on every call is worse than not offering
    it at all."""
    monkeypatch.setattr(
        "lembas.services.search.availability", lambda _key: "ddgs is not installed."
    )
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    chat = _chat_with(db, user_id, capabilities={"tools": True})
    assert "web_search" not in _names(
        tools_service.enabled_tools(db, chat, _user(db, user_id))
    )


def _context(**kwargs):
    return tools_service.ToolContext(owner_id="someone", **kwargs)


# --- The offer and the runner travel together --------------------------------
def test_the_resolved_set_carries_the_runners_with_the_schemas(db, user_id):
    """A tool that is a database row is not reachable through the import-time
    registry, so the resolution has to travel with the offer."""
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    chat = _chat_with(db, user_id, capabilities={"tools": True})
    resolved = tools_service.resolve_tools(db, chat, _user(db, user_id))

    assert _names(resolved.schemas) == set(resolved.by_name)
    assert all(callable(tool.run) for tool in resolved.defs)
    # The old accessor is the same set, so nothing that only wants schemas moved.
    assert resolved.schemas == tools_service.enabled_tools(db, chat, _user(db, user_id))


async def test_a_tool_that_was_not_offered_is_refused(db, user_id):
    """The lookup is against what was offered, not against everything that
    exists. A model naming a tool its chat was gated out of used to have it run,
    because only the offer was ever filtered."""
    from lembas.db.models import User
    from lembas.services.library import notes as notes_service

    owner = db.get(User, user_id)
    note = notes_service.create(db, owner=owner, title="Keep me", body="...")

    chat = _chat_with(db, user_id, capabilities={"tools": True, "tool_notes": False})
    resolved = tools_service.resolve_tools(db, chat, owner)
    context = tools_service.context_for(db, owner, chat, tools=resolved)

    outcome = await tools_service.run_tool(
        context, "note_manage", json.dumps({"action": "delete", "id": note.id})
    )
    assert outcome.event["status"] == "error"
    assert notes_service.get(db, note.id, owner) is not None


async def test_a_context_with_no_toolset_still_finds_the_builtins(monkeypatch):
    """None means nobody resolved a set. An empty dict does not -- it means
    nothing was offered, and is authoritative."""

    async def fake_run(_config, query, *, limit=None):
        return [SearchResult("A title", "https://a.test", "a snippet")]

    monkeypatch.setattr("lembas.services.search.run", fake_run)

    unresolved = await tools_service.run_tool(_context(), "web_search", '{"query": "x"}')
    assert unresolved.event["status"] == "ok"

    empty = await tools_service.run_tool(_context(tools={}), "web_search", '{"query": "x"}')
    assert empty.event["status"] == "error"


# --- Knowledge is scoped to the chat's bases ---------------------------------
async def test_knowledge_search_is_limited_to_the_attached_bases(db, user_id):
    """"Answer from the contracts folder" is a different question from "answer
    from everything I have ever uploaded"."""
    from lembas.db.models import User
    from lembas.services.library import documents as documents_service

    owner = db.get(User, user_id)
    trees = documents_service.create_base(db, owner=owner, name="Trees")
    contracts = documents_service.create_base(db, owner=owner, name="Contracts")
    documents_service.store_upload(
        db, owner=owner, payload=b"The mallorn is golden.", filename="a.txt",
        title="Mallorn", base=trees,
    )
    documents_service.store_upload(
        db, owner=owner, payload=b"The mallorn clause is void.", filename="b.txt",
        title="Clause", base=contracts,
    )

    everywhere = await tools_service.run_tool(
        tools_service.ToolContext(owner_id=user_id), "knowledge_search",
        '{"query": "mallorn"}',
    )
    assert {r["title"] for r in everywhere.event["results"]} == {"Mallorn", "Clause"}

    scoped = await tools_service.run_tool(
        tools_service.ToolContext(owner_id=user_id, base_ids=[contracts.id]),
        "knowledge_search",
        '{"query": "mallorn"}',
    )
    assert [r["title"] for r in scoped.event["results"]] == ["Clause"]


def test_a_chat_with_no_bases_searches_everything(db, user_id):
    """Empty means "everything the owner can see", not "nothing"."""
    from lembas.db.models import User

    chat = _chat_with(db, user_id, capabilities={"tools": True})
    context = tools_service.context_for(db, db.get(User, user_id), chat)
    assert context.base_ids == []


# --- Running one -------------------------------------------------------------
async def test_running_web_search_formats_results_for_the_model(monkeypatch):
    async def fake_run(_config, query, *, limit=None):
        return [SearchResult("A title", "https://a.test", "a snippet")]

    monkeypatch.setattr("lembas.services.search.run", fake_run)

    outcome = await tools_service.run_tool(_context(), "web_search", '{"query": "mallorn"}')
    assert "A title" in outcome.content
    assert "https://a.test" in outcome.content
    assert outcome.event["status"] == "ok"
    assert outcome.event["results"][0]["host"] == "a.test"


async def test_malformed_argument_json_is_treated_as_the_query(monkeypatch):
    """Small models emit broken argument JSON often enough that this is a normal
    path, not an exceptional one."""
    seen = {}

    async def fake_run(_config, query, *, limit=None):
        seen["query"] = query
        return []

    monkeypatch.setattr("lembas.services.search.run", fake_run)
    await tools_service.run_tool(_context(), "web_search", "mallorn tree")
    assert seen["query"] == "mallorn tree"


async def test_a_failed_search_hands_the_model_an_explanation(monkeypatch):
    """A failed search should produce "I could not look that up", not kill the
    whole reply."""

    async def fake_run(_config, _query, *, limit=None):
        raise SearchError("DuckDuckGo is rate limiting this instance.")

    monkeypatch.setattr("lembas.services.search.run", fake_run)

    outcome = await tools_service.run_tool(_context(), "web_search", '{"query": "x"}')
    assert "rate limiting" in outcome.content
    assert outcome.event["status"] == "error"


async def test_an_unknown_tool_is_reported_rather_than_raised():
    outcome = await tools_service.run_tool(_context(), "launch_missiles", "{}")
    assert "no tool called" in outcome.content
    assert outcome.event["status"] == "error"


async def test_a_call_with_no_query_is_reported():
    outcome = await tools_service.run_tool(_context(), "web_search", '{"query": "  "}')
    assert outcome.event["status"] == "error"


# --- The turns sent back -----------------------------------------------------
def test_the_assistant_turn_echoes_the_calls():
    """The endpoint needs its own tool_calls back before the tool replies, or it
    has nothing to match the tool_call_ids against."""
    turn = tools_service.assistant_turn(
        [{"id": "c1", "name": "web_search", "arguments": '{"query":"x"}'}], "Looking it up."
    )
    assert turn["role"] == "assistant"
    assert turn["content"] == "Looking it up."
    assert turn["tool_calls"][0]["id"] == "c1"
    assert turn["tool_calls"][0]["function"]["name"] == "web_search"


def test_an_empty_assistant_message_becomes_null():
    """Most endpoints reject an assistant turn whose content is an empty string
    alongside tool_calls."""
    turn = tools_service.assistant_turn([{"id": "c", "name": "n", "arguments": "{}"}], "")
    assert turn["content"] is None


def test_the_tool_turn_carries_the_call_id():
    turn = tools_service.tool_turn({"id": "c1", "name": "web_search"}, "results here")
    assert turn == {
        "role": "tool",
        "tool_call_id": "c1",
        "name": "web_search",
        "content": "results here",
    }


def test_every_schema_is_valid_json():
    """They are sent verbatim to the endpoint; a schema that will not serialise
    fails every request rather than one."""
    for name, tool in tools_service.REGISTRY.items():
        json.dumps(tool.schema)
        assert tool.schema["function"]["name"] == name
        assert tool.family in tools_service.FAMILIES


def test_every_tool_describes_when_to_use_it():
    """The description is all the model has to decide with."""
    for tool in tools_service.REGISTRY.values():
        assert len(tool.description) > 40, tool.name


# --- Fetching a page ---------------------------------------------------------------
def _offered_names(db, user_id, **capabilities):
    chat = _chat_with(db, user_id, capabilities={"tools": True, **capabilities})
    return {d.name for d in tools_service.resolve_tools(db, chat, _user(db, user_id)).defs}


def test_fetch_is_offered_by_default(db, user_id):
    assert "web_fetch" in _offered_names(db, user_id)


def test_fetch_needs_the_model_capability(db, user_id):
    assert "web_fetch" not in _offered_names(db, user_id, tool_fetch=False)


def test_fetch_needs_the_instance_switch(db, user_id):
    """Separate from the link-attach path on purpose: an administrator can stop
    a model choosing an address while somebody attaching one still works."""
    settings_store.update(db, {"fetch_enabled": False}, key=settings_store.SEARCH)
    assert "web_fetch" not in _offered_names(db, user_id)


def test_fetch_needs_the_permission(db, user_id):
    user = _user(db, user_id)
    user.role = "user"  # administrators pass everything
    settings_store.update(db, {"default_permissions": {"tools.fetch": False}})
    db.commit()

    chat = _chat_with(db, user_id, capabilities={"tools": True})
    assert "web_fetch" not in {d.name for d in tools_service.resolve_tools(db, chat, user).defs}


def test_fetch_does_not_need_the_library_permission(db, user_id):
    """It has nothing to do with anybody's own documents and notes, the same
    argument custom tools and MCP already make."""
    user = _user(db, user_id)
    user.role = "user"
    settings_store.update(db, {"default_permissions": {"library.use": False}})
    db.commit()

    chat = _chat_with(db, user_id, capabilities={"tools": True})
    assert "web_fetch" in {d.name for d in tools_service.resolve_tools(db, chat, user).defs}


async def test_a_failed_fetch_does_not_kill_the_reply(monkeypatch):
    from lembas.services import fetch as fetch_service

    async def boom(*_args, **_kwargs):
        raise fetch_service.FetchError("That address is not reachable.")

    monkeypatch.setattr(fetch_service, "fetch", boom)

    outcome = await tools_service.run_tool(
        _context(), "web_fetch", '{"url": "https://a.test/"}'
    )
    assert outcome.event["status"] == "error"
    assert "not reachable" in outcome.content


async def test_a_long_knowledge_document_is_cut_and_the_model_told(db, user_id):
    """It was the one reader with no bound at all. `fetch` caps and says so,
    `file_read` caps and says so, the memories block, the skill index and the
    project listing all carry budgets -- and `knowledge_get` returned
    `extracted_text` whole. Its description said "in full", which is why this
    read as correct: the tool did exactly what it claimed, and what it claimed
    was one call away from filling the window with nothing reporting it.
    """
    from lembas.db.models import User
    from lembas.services.library import documents as documents_service

    owner = db.get(User, user_id)
    base = documents_service.create_base(db, owner=owner, name="Long")
    document = documents_service.store_upload(
        db, owner=owner, payload=b"x" * 90_000, filename="big.txt",
        title="Big", base=base,
    )

    outcome = await tools_service.run_tool(
        tools_service.ToolContext(owner_id=user_id),
        "knowledge_get",
        json.dumps({"id": document.id}),
    )
    assert len(outcome.content) < tools_service.MAX_DOCUMENT_CHARS + 500
    assert "Cut off here" in outcome.content
    assert outcome.event["truncated"] is True


async def test_a_short_knowledge_document_is_returned_whole(db, user_id):
    """The cap must not become a truncation notice on ordinary documents."""
    from lembas.db.models import User
    from lembas.services.library import documents as documents_service

    owner = db.get(User, user_id)
    base = documents_service.create_base(db, owner=owner, name="Short")
    document = documents_service.store_upload(
        db, owner=owner, payload=b"The mallorn is golden.", filename="a.txt",
        title="Mallorn", base=base,
    )

    outcome = await tools_service.run_tool(
        tools_service.ToolContext(owner_id=user_id),
        "knowledge_get",
        json.dumps({"id": document.id}),
    )
    assert "The mallorn is golden." in outcome.content
    assert "Cut off here" not in outcome.content
    assert "truncated" not in outcome.event


async def test_a_long_page_is_cut_and_the_model_told(monkeypatch):
    """120_000 characters is roughly thirty thousand tokens. One call would fill
    an ordinary window and spend an agent chat's whole output budget."""
    from lembas.services import fetch as fetch_service

    async def big(*_args, **_kwargs):
        return fetch_service.Fetched(
            url="https://a.test/", title="Long", text="x" * 100_000, truncated=False
        )

    monkeypatch.setattr(fetch_service, "fetch", big)

    outcome = await tools_service.run_tool(_context(), "web_fetch", '{"url": "https://a.test/"}')
    assert len(outcome.content) < tools_service.MAX_FETCH_CHARS + 500
    assert "call again with offset 20000" in outcome.content


async def test_a_long_page_is_read_in_parts_with_offset(monkeypatch):
    """The harness spec's `web_fetch`: a long page comes in parts, and the
    model continues with `offset` -- the LLeMbas CLI's page size and wording."""
    from lembas.services import fetch as fetch_service

    text = "a" * 20_000 + "b" * 20_000 + "c" * 5_000

    async def big(*_args, **_kwargs):
        return fetch_service.Fetched(url="https://a.test/", title="Long", text=text)

    monkeypatch.setattr(fetch_service, "fetch", big)

    first = await tools_service.run_tool(_context(), "web_fetch", '{"url": "https://a.test/"}')
    assert first.content.startswith("# Long\nhttps://a.test/\n\n")
    assert "(25000 more characters — call again with offset 20000)" in first.content
    second = await tools_service.run_tool(
        _context(), "web_fetch", '{"url": "https://a.test/", "offset": 20000}'
    )
    assert "b" * 20_000 in second.content and "aaaa" not in second.content
    assert "call again with offset 40000" in second.content
    last = await tools_service.run_tool(
        _context(), "web_fetch", '{"url": "https://a.test/", "offset": 40000}'
    )
    assert last.content.endswith("c" * 5_000)


async def test_a_page_cut_at_the_fetch_ceiling_says_so_on_its_last_part(monkeypatch):
    from lembas.services import fetch as fetch_service

    async def capped(*_args, **_kwargs):
        return fetch_service.Fetched(url="https://a.test/", title="", text="x", truncated=True)

    monkeypatch.setattr(fetch_service, "fetch", capped)
    outcome = await tools_service.run_tool(_context(), "web_fetch", '{"url": "https://a.test/"}')
    assert outcome.content.startswith("https://a.test/")
    assert "has been cut off" in outcome.content


async def test_the_model_is_given_markdown(monkeypatch):
    from lembas.services import fetch as fetch_service

    seen = {}

    async def spy(url, **kwargs):
        seen.update(kwargs)
        return fetch_service.Fetched(url=url, title="T", text="# T")

    monkeypatch.setattr(fetch_service, "fetch", spy)
    await tools_service.run_tool(_context(), "web_fetch", '{"url": "https://a.test/"}')
    assert seen["markdown"] is True
