"""The prefix stays still (harness spec 3.1.0, context.md §10).

A chat's system prompt is made once per session and resent byte for byte; no
clock is in it; what changes during the session reaches the model as a
`<session-note>` on the next user message, stored with that message and sent
identically from then on; the tool list is the same for the whole session.
"""

from __future__ import annotations

import asyncio

import pytest

from lembas.db.models import ROLE_ASSISTANT, Chat, Connection, Message, Model, User
from lembas.db.models.memory import AUTHOR_USER
from lembas.services import chat as chat_service
from lembas.services import harness_spec, prefix, settings_store
from lembas.services import memories as memories_service
from lembas.services import tools as tools_service


def _chat(db, user_id, **extra) -> Chat:
    connection = Connection(name="c", base_url="http://127.0.0.1:1", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="m", capabilities_json={"tools": True}))
    db.commit()
    chat = Chat(user_id=user_id, model_id="m", connection_id=connection.id, **extra)
    db.add(chat)
    db.commit()
    return chat


class Session:
    """Replies as `generation._run` makes them: a user turn, a placeholder,
    the request built with `persist`, and the reply written into the row."""

    def __init__(self, db, chat: Chat) -> None:
        self.db = db
        self.chat = chat
        self.user = db.get(User, chat.user_id)
        self.sent: list[dict] = []

    def tools(self) -> list[dict]:
        return tools_service.resolve_tools(self.db, self.chat, self.user).schemas

    def turn(self, words: str, answer: str = "ok") -> dict:
        db = self.db
        db.add(Message(chat_id=self.chat.id, role="user", content=words))
        db.commit()
        placeholder = Message(
            chat_id=self.chat.id,
            role=ROLE_ASSISTANT,
            content="",
            complete=False,
            model_id=self.chat.model_id,
        )
        db.add(placeholder)
        db.commit()
        body = chat_service.build_request(
            db, self.chat, upto=placeholder, tools=self.tools(), user=self.user, persist=True
        )
        db.commit()
        placeholder.content, placeholder.complete = answer, True
        db.commit()
        self.sent.append(body)
        return body


def _system(body: dict) -> str:
    assert body["messages"][0]["role"] == "system"
    return body["messages"][0]["content"]


def _last_user(body: dict) -> str:
    return next(m["content"] for m in reversed(body["messages"]) if m["role"] == "user")


@pytest.fixture
def day(monkeypatch):
    """The date in the person's zone, set by the test."""
    state = {"today": "Thursday 5 November 2026"}
    monkeypatch.setattr(prefix, "today", lambda user: state["today"])
    return state


# --- Rule 1 and 2: made once, no clock -------------------------------------------------
def test_across_midnight_the_system_prompt_is_identical_and_the_date_is_a_note(
    db, user_id, day
):
    session = Session(db, _chat(db, user_id))
    first = session.turn("Hello.")
    day["today"] = "Friday 6 November 2026"
    second = session.turn("Still there?")
    third = session.turn("And now?")

    assert _system(first) == _system(second) == _system(third)
    assert "2026" not in _system(first)
    note = harness_spec.note()
    assert _last_user(first) == (
        f"Hello.\n\n{note['open']}\nToday is Thursday 5 November 2026.\n{note['close']}"
    )
    assert _last_user(second).endswith(
        f"{note['open']}\nToday is Friday 6 November 2026.\n{note['close']}"
    )
    # Unchanged since: no note at all.
    assert _last_user(third) == "And now?"
    # And every turn already sent goes out exactly as it did.
    assert second["messages"][: len(first["messages"])] == first["messages"]
    assert third["messages"][1:4] == [
        first["messages"][1],
        {"role": "assistant", "content": "ok"},
        second["messages"][3],
    ]


def test_a_note_once_sent_is_never_recomputed(db, user_id, day):
    session = Session(db, _chat(db, user_id))
    first = session.turn("Hello.")
    day["today"] = "Saturday 7 November 2026"
    memories_service.add(db, owner=session.user, content="Likes tea.", author=AUTHOR_USER)
    db.commit()
    later = session.turn("Next.")
    assert later["messages"][1] == first["messages"][1]
    stored = db.get(Message, db.query(Message).filter_by(content="Hello.").one().id)
    assert stored.content == "Hello."  # the note is never the person's words
    assert "Today is" in stored.note_json["m|"]["text"]


# --- Rule 3: a change during the session is a note ------------------------------------
def test_a_memory_the_person_adds_is_a_note_not_a_prompt_change(db, user_id, day):
    session = Session(db, _chat(db, user_id))
    first = session.turn("Hello.")
    memories_service.add(db, owner=session.user, content="Prefers metric units.",
                         author=AUTHOR_USER)
    db.commit()
    second = session.turn("Convert a mile.")

    assert _system(second) == _system(first)
    assert "Prefers metric units." not in _system(second)
    words = _last_user(second)
    assert words.startswith("Convert a mile.\n\n<session-note>\n")
    assert "The person changed what is remembered; it is now:\n- Prefers metric units." in words
    assert "Today is" not in words  # the date has not changed


def test_a_memory_the_model_saved_itself_is_no_note(db, user_id, day):
    session = Session(db, _chat(db, user_id))
    session.turn("Remember I like tea.")
    reply = db.query(Message).filter_by(role=ROLE_ASSISTANT).one()
    memories_service.add(db, owner=session.user, content="Likes tea.")
    db.commit()
    prefix.known(db, session.chat, session.user,
                 chat_service.speaker_for(db, session.chat, reply), reply, memory=True)
    db.commit()
    assert _last_user(session.turn("Thanks.")) == "Thanks."


def test_a_personality_chosen_during_the_session_is_a_note(db, user_id, day):
    from lembas.services import personalization

    settings_store.update(db, {"personalization_enabled": True})
    session = Session(db, _chat(db, user_id))
    first = session.turn("Hello.")
    user = session.user
    user.settings_json = {
        **(user.settings_json or {}),
        "personalization": {"on": True, "personality": "concise", "instructions": "Be brief."},
    }
    db.commit()
    instructions, personality = personalization.parts(db, user)
    assert personality and instructions == "Be brief."
    second = session.turn("Again.")

    assert _system(second) == _system(first)
    words = _last_user(second)
    assert f"The person chose a different personality:\n{personality}" in words
    assert f"the person's own instructions changed; it now reads:\n{instructions}" in words
    # One block per change, a blank line between them.
    assert "\n\nThe person chose a different personality" in words


def test_plan_mode_switched_by_the_person_is_a_note(db, user_id, day):
    session = Session(db, _chat(db, user_id))
    first = session.turn("Hello.")
    session.chat.agent_mode = "plan"
    db.commit()
    second = session.turn("Plan it first.")
    session.chat.agent_mode = ""
    db.commit()
    third = session.turn("Go.")

    assert _system(first) == _system(second) == _system(third)
    assert first["tools"] == second["tools"] == third["tools"]
    assert "The permission mode is now plan:\nPermission mode: plan." in _last_user(second)
    assert (
        f"The permission mode is now {prefix.NORMAL_MODE}:\n"
        "Plan mode has ended: make the changes the work needs; nothing is refused for "
        "planning any more."
        in _last_user(third)
    )


def test_a_plan_the_model_wrote_comes_back_on_the_next_turn(db, user_id, day):
    """A chat here does not replay an earlier reply's tool results, so the ids
    `plan_update` takes would be gone; the plan comes back as a note, in the
    model's own words rather than as the person's change."""
    from lembas.services import plans

    session = Session(db, _chat(db, user_id))
    session.turn("Make a plan.")
    session.chat.plan_json = plans.normalise({"title": "Fix it", "phases": [
        {"title": "Code", "tasks": ["Change the line"]}]})
    db.commit()
    words = _last_user(session.turn("Carry on."))
    assert "Your plan, as your plan tools left it:\n# Fix it" in words
    assert "[t1]" in words
    assert "The person changed the plan" not in words


def test_a_plan_the_person_chose_is_their_change(db, user_id, day):
    from lembas.services import plans

    session = Session(db, _chat(db, user_id))
    session.turn("Make a plan.")
    plan = plans.normalise({"title": "Older plan", "phases": [{"title": "A", "tasks": ["x"]}]})
    session.chat.plan_json = plan
    prefix.person_changed_plan(session.chat, plan)
    db.commit()
    assert "The person changed the plan; it is now:\n# Older plan" in _last_user(
        session.turn("Do that one.")
    )


# --- What makes the prompt again ------------------------------------------------------
def test_the_persons_own_edit_of_the_chats_prompt_makes_it_again(db, user_id, day):
    session = Session(db, _chat(db, user_id))
    first = session.turn("Hello.")
    session.chat.system_prompt = "Speak as Gandalf."
    db.commit()
    second = session.turn("Again.")
    assert _system(second) != _system(first)
    assert "Speak as Gandalf." in _system(second)


def test_an_instance_prompt_edited_during_a_session_waits(db, user_id, day):
    session = Session(db, _chat(db, user_id))
    first = session.turn("Hello.")
    settings_store.update(db, {"system_prompt": "Be terse."})
    assert _system(session.turn("Again.")) == _system(first)


def test_each_speaker_keeps_its_own(db, user_id, day):
    session = Session(db, _chat(db, user_id))
    session.turn("Hello.")
    other = Model(connection_id=session.chat.connection_id, model_id="n",
                  capabilities_json={"tools": True})
    db.add(other)
    session.chat.model_id = "n"
    db.commit()
    body = session.turn("Now you.")
    frozen = session.chat.system_frozen
    assert {"m|", "n|"} <= set(frozen)
    # A new speaker's session starts: its first new turn carries the date, and
    # the turn the other model already answered is sent as it was, with none.
    assert "Today is" in _last_user(body)
    assert body["messages"][1]["content"].startswith("Hello.")


def test_the_inspector_writes_nothing(db, user_id, day):
    chat = _chat(db, user_id)
    db.add(Message(chat_id=chat.id, role="user", content="Hello."))
    db.commit()
    body = chat_service.build_request(db, chat, user=db.get(User, user_id))
    db.commit()
    db.expire_all()
    chat = db.get(Chat, chat.id)
    assert not chat.system_frozen
    assert not db.query(Message).one().note_json
    assert "Today is" in _last_user(body)  # what would go


def test_a_turn_taken_in_mid_reply_is_never_given_a_note_later(db, user_id, day):
    session = Session(db, _chat(db, user_id))
    session.turn("Hello.")
    taken = Message(chat_id=session.chat.id, role="user", content="Also this.")
    db.add(taken)
    db.commit()
    prefix.sent_without_note(db, session.chat, taken, chat_service.speaker_for(db, session.chat))
    db.commit()
    day["today"] = "Friday 6 November 2026"
    body = session.turn("Next.")
    assert {"role": "user", "content": "Also this."} in body["messages"] or any(
        m["content"].startswith("Also this.") and "session-note" not in m["content"]
        for m in body["messages"]
        if m["role"] == "user"
    )


def test_the_title_is_taken_from_the_words_not_the_note(db, user_id, day):
    from lembas.services import generation

    session = Session(db, _chat(db, user_id))
    assert generation._question_from(session.turn("What is lembas?")) == "What is lembas?"


# --- Rule 4: the tool list is the session's -------------------------------------------
def test_python_is_offered_without_a_page_and_answers_unavailable(db, user_id, monkeypatch):
    from lembas.services import pages, pyodide_files
    from lembas.services.tools import files as file_tools

    monkeypatch.setattr(pyodide_files, "available", lambda: True)
    settings_store.update(db, {"pyodide_enabled": True})
    chat = _chat(db, user_id)
    user = db.get(User, user_id)
    assert not pages.attached(chat.id)

    toolset = tools_service.resolve_tools(db, chat, user)
    assert "python" in toolset.by_name

    context = tools_service.context_for(db, user, chat, tools=toolset)
    outcome = asyncio.run(tools_service.run_tool(context, "python", '{"code": "1+1"}'))
    assert outcome.content == file_tools.python_unavailable()

    # Off on the instance: not offered at all (fixed before the session).
    settings_store.update(db, {"pyodide_enabled": False})
    assert "python" not in tools_service.resolve_tools(db, chat, user).by_name


def test_python_is_not_offered_where_nobody_opens_a_page(db, user_id, monkeypatch):
    from lembas.services import pyodide_files

    monkeypatch.setattr(pyodide_files, "available", lambda: True)
    settings_store.update(db, {"pyodide_enabled": True})
    task = _chat(db, user_id, unattended=True)
    assert "python" not in tools_service.resolve_tools(db, task, db.get(User, user_id)).by_name


# --- Rule 6: cached tokens, per provider ------------------------------------------------
@pytest.mark.parametrize(
    ("chunk", "cached"),
    [
        # OpenAI, OpenRouter and llama.cpp: prompt_tokens_details.cached_tokens.
        ({"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 5,
                                   "prompt_tokens_details": {"cached_tokens": 90}}}, 90),
        # An older llama.cpp: only `timings.cache_n` beside the usage.
        ({"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 5},
          "timings": {"cache_n": 80, "prompt_n": 20}}, 80),
        # DeepSeek.
        ({"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 5,
                                   "prompt_cache_hit_tokens": 70,
                                   "prompt_cache_miss_tokens": 30}}, 70),
    ],
)
def test_cached_tokens_are_read_from_every_shape(chunk, cached):
    from lembas.services.llm.openai_client import chunk_usage

    assert chunk_usage(chunk)["cached_tokens"] == cached
