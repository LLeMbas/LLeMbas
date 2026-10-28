"""The operational preamble, and how it sits beside the authored prompt."""

from __future__ import annotations

import pytest

from lembas.db.models import Chat, Connection, Model, User
from lembas.security.passwords import hash_password
from lembas.services import chat as chat_service
from lembas.services import fs, harness, prompts, settings_store
from lembas.services import memories as memories_service
from lembas.services import skill_format as fmt
from lembas.services import tools as tools_service


@pytest.fixture
def owner(db):
    user = User(name="Frodo", email="f@shire.test", password_hash=hash_password("x"))
    db.add(user)
    db.commit()
    return user


def _tools(*names):
    return [tools_service.REGISTRY[name].schema for name in names]


# --- Composition -------------------------------------------------------------
def test_no_tools_means_no_tool_guidance(db, owner):
    """The core fragments still go out -- a model with no tools has no clock
    either, and telling it the date is not "tokens that say nothing". What it
    must not get is instructions about tools it was never offered."""
    text = harness.compose(db, owner, [])
    assert "Today is" in text
    assert "You have tools" not in text
    assert "Look things up" not in text
    assert harness.compose(db, owner, None) == text


def test_the_date_line_names_a_zone_when_nobody_has_chosen_one(db, owner):
    """The shipped default, on the shipped configuration.

    `clock.name_for` returns "" for an account that has never chosen a zone,
    which is every account until somebody visits the settings page. The variable
    sits *inside* a sentence, and `substitute` drops a line only when the whole
    line is blank after expansion -- so an unresolved zone was never a dropped
    line, it was "in  unless", shipped on every request. Both the comment in
    `harness.py` and the fragment's own hint claimed otherwise, and no test
    asked.
    """
    assert not (owner.settings_json or {}).get("timezone")
    text = harness.compose(db, owner, [])

    line = next(ln for ln in text.splitlines() if "Times the person gives you" in ln)
    assert "in  unless" not in line
    assert "  " not in line
    # Whatever the host is set to, it has to be *something*.
    zone = line.split(" are in ", 1)[1].split(" unless", 1)[0]
    assert zone.strip()


def test_the_date_line_uses_the_readers_own_zone_when_they_have_one(db, owner):
    """And a chosen zone is preferred to the server's, which is the whole point
    of the variable -- the fallback must not have flattened it."""
    owner.settings_json = {**(owner.settings_json or {}), "timezone": "Pacific/Auckland"}
    db.commit()
    text = harness.compose(db, owner, [])
    assert "are in Pacific/Auckland unless" in text


def test_clearing_the_core_fragments_restores_an_empty_harness(db, owner):
    """The behaviour change is a default, not a rule: an administrator who wants
    nothing sent to a tool-less model can still have exactly that."""
    prompts.save(
        db,
        {f.key: "" for f in prompts.BUILTIN if f.group == prompts.GROUP_CORE},
    )
    assert harness.compose(db, owner, []) == ""


def test_only_the_guidance_for_offered_tools_appears(db, owner):
    text = harness.compose(db, owner, _tools("web_search"))
    assert "Look things up" in text
    assert "You keep notes" not in text
    assert "Skills are procedures" not in text


def test_a_custom_tools_guidance_appears_only_when_it_is_offered(db, owner):
    """The row supplies the default, and the fragment is gated on the tool's own
    family -- which is why the registry had to stop being a module constant."""
    from lembas.db.models import CustomTool

    db.add(
        CustomTool(
            slug="weather",
            name="Weather",
            description="Look up the weather.",
            url_template="https://api.test/{{city}}",
            guidance="- Check the weather rather than guessing at it.",
        )
    )
    db.commit()

    offered = tools_service.registry(db)["weather"].schema
    assert "Check the weather" in harness.compose(db, owner, [offered])
    assert "Check the weather" not in harness.compose(db, owner, _tools("web_search"))


def test_the_memory_block_is_included_when_memory_is_offered(db, owner):
    memories_service.add(db, owner=owner, content="Prefers metric units.")
    text = harness.compose(db, owner, _tools("memory"))
    assert "What you know about this person" in text
    assert "Prefers metric units." in text


def test_memories_are_absent_without_the_memory_tool(db, owner):
    """A model not given the memory tool has no business being told them."""
    memories_service.add(db, owner=owner, content="Prefers metric units.")
    text = harness.compose(db, owner, _tools("web_search"))
    assert "Prefers metric units." not in text


def _skill(db, owner, name: str, description: str, body: str) -> None:
    home = fs.ensure_home(db, owner).path
    fs.write(fs.view(db, owner), f"{home}/skills/{name}/SKILL.md",
             fmt.compose(name, description, body).encode())
    db.commit()


def test_the_skill_index_is_names_and_descriptions_only(db, owner):
    _skill(db, owner, "weekly-report", "When asked.", "SECRET")
    text = harness.compose(db, owner, _tools("skill_view"))
    assert "weekly-report: When asked." in text
    assert "SECRET" not in text


def test_an_empty_store_contributes_no_heading(db, owner):
    """And the guidance must not point at a heading that is not there: telling a
    model to consult an absent section is a good way to make it invent one."""
    text = harness.compose(db, owner, _tools("memory", "skill_view"))
    assert "### What you know about this person" not in text
    assert "### Skills available" not in text
    assert "was remembered earlier" not in text
    assert "You can remember durable facts" in text


def test_the_harness_is_capped(db, owner, monkeypatch):
    monkeypatch.setattr(harness, "MAX_HARNESS_CHARS", 200)
    for index in range(50):
        _skill(db, owner, f"skill-{index}", "x" * 200, "y")
    assert len(harness.compose(db, owner, _tools("skill_view"))) <= 202


# --- Joining -----------------------------------------------------------------
def test_the_authored_prompt_comes_last():
    """It is closest to the conversation, and it is what the user actually
    wrote."""
    joined = harness.join("HARNESS", "AUTHORED")
    assert joined.index("HARNESS") < joined.index("AUTHORED")


def test_either_half_alone_is_returned_unchanged():
    assert harness.join("", "AUTHORED") == "AUTHORED"
    assert harness.join("HARNESS", "") == "HARNESS"
    assert harness.join("", "") == ""


# --- Through build_request ---------------------------------------------------
def _chat(db, owner, *, capabilities, model_prompt="", chat_prompt=""):
    connection = Connection(name="c", base_url="http://h", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(
        Model(
            connection_id=connection.id,
            model_id="m",
            capabilities_json=capabilities,
            system_prompt=model_prompt,
        )
    )
    db.commit()
    chat = Chat(
        user_id=owner.id, model_id="m", connection_id=connection.id, system_prompt=chat_prompt
    )
    db.add(chat)
    db.commit()
    return chat


def _system(body):
    first = body["messages"][0] if body["messages"] else {}
    return first.get("content", "") if first.get("role") == "system" else ""


def test_a_request_without_tools_carries_no_tool_guidance_and_no_tools_key(db, owner):
    chat = _chat(db, owner, capabilities={})
    body = chat_service.build_request(db, chat, tools=[], user=owner)
    assert "tools" not in body
    assert "You have tools" not in _system(body)


def test_the_harness_precedes_the_authored_prompt(db, owner):
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    chat = _chat(db, owner, capabilities={"tools": True}, chat_prompt="Speak as Gandalf.")
    offered = tools_service.enabled_tools(db, chat, owner)

    system = _system(chat_service.build_request(db, chat, tools=offered, user=owner))
    assert system.index("How to work") < system.index("Speak as Gandalf.")


def test_precedence_between_the_authored_layers_is_untouched(db, owner):
    """The harness is a different axis. Exactly one authored layer still wins,
    and it is still the most specific one."""
    settings_store.update(db, {"system_prompt": "Instance."})
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)

    chat = _chat(
        db, owner, capabilities={"tools": True}, model_prompt="Model.", chat_prompt="Chat."
    )
    offered = tools_service.enabled_tools(db, chat, owner)
    system = _system(chat_service.build_request(db, chat, tools=offered, user=owner))

    assert "Chat." in system
    assert "Model." not in system
    assert "Instance." not in system
    # And the resolver on its own is unchanged.
    assert chat_service.effective_system_prompt(db, chat) == "Chat."


def test_a_model_prompt_wins_when_the_chat_has_none(db, owner):
    settings_store.update(db, {"system_prompt": "Instance."})
    chat = _chat(db, owner, capabilities={}, model_prompt="Model.")
    system = _system(chat_service.build_request(db, chat, user=owner))
    assert system.endswith("Model.")
    assert "Instance." not in system
    assert chat_service.effective_system_prompt(db, chat) == "Model."


def test_the_seam_line_only_appears_when_there_is_something_to_hand_over_to(db, owner):
    """It introduces the authored prompt. With no authored prompt it would be
    pointing at nothing, which is the failure the whole `requires` idea exists
    to avoid."""
    bare = _chat(db, owner, capabilities={})
    assert "was written by whoever set up" not in _system(
        chat_service.build_request(db, bare, user=owner)
    )

    authored = _chat(db, owner, capabilities={}, chat_prompt="Speak as Gandalf.")
    assert "was written by whoever set up" in _system(
        chat_service.build_request(db, authored, user=owner)
    )


def test_an_administrators_wording_replaces_the_default(db, owner):
    prompts.save(db, {"core.today": "The date is {{today}}, more or less."})
    text = harness.compose(db, owner, [])
    assert "more or less." in text
    assert "Your training data stops well before this" not in text


def test_the_attached_files_are_named_and_explained(db, owner):
    from lembas.db.models import Attachment

    chat = _chat(db, owner, capabilities={})
    db.add(
        Attachment(
            user_id=owner.id,
            chat_id=chat.id,
            filename="report.txt",
            stored_name="x.txt",
            media_type="text/plain",
            size_bytes=10,
            kind="text",
        )
    )
    db.commit()

    text = harness.compose(db, owner, [], chat)
    assert "report.txt" in text
    assert "<document name=" in text


def test_a_chat_with_no_attachments_says_nothing_about_documents(db, owner):
    chat = _chat(db, owner, capabilities={})
    assert "<document name=" not in harness.compose(db, owner, [], chat)


def test_the_tools_array_rides_along(db, owner):
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    chat = _chat(db, owner, capabilities={"tools": True})
    offered = tools_service.enabled_tools(db, chat, owner)
    body = chat_service.build_request(db, chat, tools=offered, user=owner)
    assert body["tools"] == offered


# --- The project listing -----------------------------------------------------
# (An agent chat runs on a linked CLI, whose own prompt carries it. A plain
# chat is told nothing about files.)
def test_a_plain_chat_is_told_nothing_about_files(db, owner):
    chat = Chat(user_id=owner.id)
    db.add(chat)
    db.commit()

    assert "Files in" not in harness.compose(db, owner, _tools("web_search"), chat=chat)


# --- One round, or as many as it takes ----------------------------------------
def test_a_plain_chat_is_told_its_real_ceiling(db, owner):
    """The number it is actually given, not a constant -- a model told it has
    five rounds and cut off after three has been lied to about its own budget."""
    settings_store.update(db, {"max_chat_rounds": 3})
    text = harness.compose(db, owner, _tools("web_search"))

    assert "at most 3 rounds" in text


def test_no_ceiling_means_no_round_budget_is_claimed(db, owner):
    """Zero is "no ceiling", and a fragment promising zero rounds would be worse
    than none at all."""
    settings_store.update(db, {"max_chat_rounds": 0})
    text = harness.compose(db, owner, _tools("web_search"))

    # `core.interjection` also mentions rounds, so this asserts on the budget
    # sentence rather than on the word.
    assert "at most" not in text


def test_the_fetch_guidance_appears_only_with_the_tool(db, owner):
    assert "read one web page at a time" in harness.compose(db, owner, _tools("web_fetch"))
    assert "read one web page at a time" not in harness.compose(db, owner, _tools("web_search"))


# --- The ceiling has to fit what the defaults already grant --------------------
def test_the_shipped_defaults_fit_under_the_ceiling(db, owner):
    """A chat's whole preamble with every family on, at the budgets this ships
    with: the shared system text, every tool family's guidance, a plan, the
    memories and a skill index.

    It once did not fit, when the agent surface lived here: the fragments plus
    a project listing and an AGENTS.md crossed the ceiling, and `assemble` cuts
    the tail -- the project's own instructions -- silently. The agent surface
    is the linked LLeMbas CLI's now; what has to fit is what is left, and the
    memories and the plan are the tail that would go first.
    """
    from lembas.services import tools as tools_service

    # Every name the harness knows about, so a variable added later is covered
    # here without anybody remembering to add it.
    variables = dict.fromkeys(harness.context_variables(db, owner, [], None), "")
    names = ", ".join(tools_service.REGISTRY)
    variables.update(
        {
            "today": "Monday 3 August 2026",
            "instance_name": "LLeMbas",
            "user_name": "Frodo",
            "intro": harness.intro_sentence("Frodo"),
            "surface": harness.SURFACE,
            "tool_names": names,
            "tool_list": harness.tool_list_sentence(names),
            "max_rounds": "200",
            "prototypes": "yes",
            "prototype_sources": "Nothing can be loaded from the internet.",
            "mode_plan": "yes",
            # The files block at a size a real chat reaches: a dozen places.
            "start": "`/home/frodo/projects/ring`",
            "view": ", ".join(f"`/home/frodo/place-{n}/` (stage)" for n in range(12)),
            "show": "Open a file with show.",
            "python": "Python runs in the browser.",
            "document_names": "report.pdf, notes.txt",
            "image_templates": "- portrait: a portrait",
            "image_models": "sdxl",
            # Each at a size a real chat reaches: a full plan, the memories at
            # their whole budget (`memories.MAX_TOTAL_CHARS`), a long skill
            # index.
            "plan": "P" * 1200,
            "plan_context": "### The current plan\n\n" + "P" * 1200,
            "memories": "M" * 4000,
            "skills": "S" * 2000,
        }
    )

    out = harness.compose_from(
        db,
        variables=variables,
        families=list(tools_service.FAMILIES),
        has_tools=True,
        overrides={},
    )

    assert not out.endswith("…"), f"the preamble was truncated at {len(out):,} characters"
    assert "M" * 100 in out, "the memories were cut off"
    assert "S" * 100 in out, "the skill index was cut off"

    # Fitting is not enough. It fitted with 1,300 characters to spare once, and
    # a ceiling that close to the content is one the next fragment crosses --
    # silently, and by cutting the tail, which is the project's own AGENTS.md.
    # The margin is also what an administrator's own wording goes in: an
    # override is usually longer than the default it replaces.
    room = harness.MAX_HARNESS_CHARS - len(out)
    assert room >= harness.MAX_HARNESS_CHARS * harness.HARNESS_MARGIN, (
        f"only {room:,} characters of headroom left under "
        f"{harness.MAX_HARNESS_CHARS:,}; raise the ceiling or shorten a fragment"
    )
