"""Prompt fragments: their variables, their gates, and what gets stored."""

from __future__ import annotations

import pytest

from lembas.services import prompts, settings_store


@pytest.fixture
def extra_source():
    """Register a fragment source for one test, then take it away again.

    The registry is module state, so a test that adds to it and does not clean up
    leaks into every test after it.

    Restored to **what was there**, not to `[_builtin_source]`. Resetting to the
    builtin alone also threw away `services/tools:_row_source`, registered at
    import -- so after the first test using this fixture, no custom tool or MCP
    server contributed a fragment for the rest of the process, and
    `test_a_custom_tools_guidance_appears_only_when_it_is_offered` passed or
    failed on file ordering alone. A teardown that quietly removes production
    wiring is worse than no teardown, because the suite still goes green.
    """
    before = list(prompts._SOURCES)

    def install(*fragments: prompts.Fragment):
        prompts.register_source(lambda db: fragments)

    yield install
    prompts._SOURCES[:] = before


# --- Substitution ------------------------------------------------------------
def test_a_known_variable_is_replaced():
    assert prompts.substitute("Hello {{user_name}}.", {"user_name": "Frodo"}) == "Hello Frodo."


def test_whitespace_inside_the_braces_is_allowed():
    assert prompts.substitute("{{ user_name }}", {"user_name": "Frodo"}) == "Frodo"


def test_an_unknown_name_passes_through_exactly_as_typed():
    """The fallback the whole syntax choice rests on: a collision with real
    prompt text degrades to "you get what you wrote"."""
    text = 'Reply as {"total": 1}, not {{Foo}} or {{a-b}} or ${PATH} or {{unknown}}.'
    assert prompts.substitute(text, {"user_name": "Frodo"}) == text


def test_a_known_but_empty_variable_becomes_nothing():
    """Not a pass-through. Pass-through is for names that are not variables, not
    for variables that happen to be empty -- otherwise an account with no name
    would send the literal braces to the model."""
    assert prompts.substitute("Talking to {{user_name}}.", {"user_name": ""}) == "Talking to ."


def test_adjacent_variables_both_expand():
    assert prompts.substitute("{{a}}{{b}}", {"a": "1", "b": "2"}) == "12"


def test_a_substituted_value_is_never_rescanned():
    """A security property, not an accident: {{memories}} carries text a model
    wrote, and a memory reading "{{skills}}" must not pull in the skill index."""
    assert prompts.substitute("{{memories}}", {"memories": "{{skills}}", "skills": "SECRET"}) == (
        "{{skills}}"
    )


def test_a_line_that_was_only_a_variable_disappears():
    """So an empty value leaves no hole and no stranded heading."""
    assert prompts.substitute("before\n{{memories}}\nafter", {"memories": ""}) == "before\nafter"


def test_a_line_with_no_variable_is_left_alone_even_when_blank():
    assert prompts.substitute("a\n\nb", {}) == "a\n\nb"


# --- Gates -------------------------------------------------------------------
def _assembled(db, **kwargs):
    return prompts.assemble(db, groups=(prompts.GROUP_CORE, prompts.GROUP_TOOLS), **kwargs)


def test_requires_skips_the_whole_fragment(db, extra_source):
    extra_source(
        prompts.Fragment(
            key="core.zz_test",
            label="t",
            group=prompts.GROUP_CORE,
            order=900,
            default="Bases: {{knowledge_bases}}",
            requires=("knowledge_bases",),
        )
    )
    assert "Bases:" not in _assembled(db, variables={"knowledge_bases": ""})
    assert "Bases: contracts" in _assembled(db, variables={"knowledge_bases": "contracts"})


def test_families_gate_a_fragment(db, extra_source):
    extra_source(
        prompts.Fragment(
            key="tool.zz_test",
            label="t",
            group=prompts.GROUP_TOOLS,
            order=900,
            default="- gated",
            families=("web_search",),
        )
    )
    assert "- gated" not in _assembled(db, variables={}, families=("notes",))
    assert "- gated" in _assembled(db, variables={}, families=("web_search",))


def test_when_tools_gates_a_fragment(db):
    text = _assembled(db, variables={}, has_tools=False)
    assert "You have tools" not in text
    assert "You have tools" in _assembled(db, variables={}, has_tools=True)


def test_a_run_of_bullets_stays_a_single_list(db):
    """Five guidance fragments are five lines, not eleven."""
    text = prompts.assemble(
        db,
        groups=(prompts.GROUP_TOOLS,),
        variables={"memory_limit": "400"},
        families=("web_search", "memory"),
    )
    assert "\n\n- " not in text
    assert text.count("\n- ") == 1


def test_assembly_is_capped(db):
    assert len(_assembled(db, variables={}, has_tools=True, limit=100)) <= 102


# --- Storage -----------------------------------------------------------------
def test_an_override_wins_over_the_default(db):
    prompts.save(db, {"core.heading": "## Rules"})
    assert prompts.resolve(db, "core.heading") == "## Rules"
    assert prompts.is_overridden(db, "core.heading")


def test_an_empty_override_turns_a_fragment_off(db):
    prompts.save(db, {"core.style": ""})
    assert "Answer in the language" not in _assembled(db, variables={})


def test_text_equal_to_the_default_is_not_stored(db):
    """So that improving a default in a later release still reaches an instance
    whose administrator opened the page and pressed Save."""
    default = prompts.catalogue(db)["core.heading"].default
    prompts.save(db, {"core.heading": default})
    assert prompts.stored(db) == {}
    assert not prompts.is_overridden(db, "core.heading")


def test_the_line_endings_a_browser_submits_do_not_count_as_an_edit(db):
    """A textarea posts CRLF. Without normalising, every fragment would read as
    edited the moment the page was saved once."""
    default = prompts.catalogue(db)["context.memories"].default
    prompts.save(db, {"context.memories": default.replace("\n", "\r\n")})
    assert prompts.stored(db) == {}


def test_restoring_the_default_text_removes_the_override(db):
    default = prompts.catalogue(db)["core.heading"].default
    prompts.save(db, {"core.heading": "## One"})
    prompts.save(db, {"core.heading": default})
    assert prompts.stored(db) == {}


def test_a_fragment_that_was_not_submitted_keeps_its_override(db):
    """A fragment can be missing from the page because whatever contributes it
    is switched off -- a disabled custom tool. A save must not throw its wording
    away just for not having been on screen."""
    prompts.save(db, {"core.heading": "## One", "core.style": "Two"})
    prompts.save(db, {"core.heading": "## One"})
    assert set(prompts.stored(db)) == {"core.heading", "core.style"}


def test_plain_settings_in_the_group_survive_a_save(db):
    settings_store.update(db, {"max_harness_chars": 500}, key=settings_store.PROMPTS)
    prompts.save(db, {"core.heading": "## One"})
    assert settings_store.get(db, "max_harness_chars", key=settings_store.PROMPTS) == 500


def test_clear_returns_every_fragment_to_its_default(db):
    prompts.save(db, {"core.heading": "## One"})
    prompts.clear(db)
    assert prompts.stored(db) == {}
    assert prompts.resolve(db, "core.heading") == "## How to work"


def test_overrides_can_be_supplied_without_touching_the_database(db):
    """What the admin page previews unsaved text with."""
    text = _assembled(db, variables={}, overrides={"core.heading": "## Draft"})
    assert "## Draft" in text
    assert prompts.stored(db) == {}


# --- The custom-tool seam ----------------------------------------------------
def test_a_registered_source_needs_no_change_anywhere_else(db, extra_source):
    """The one requirement the whole design exists for: when custom tools land,
    a tool contributes its guidance by registering a source and nothing else."""
    extra_source(
        prompts.Fragment(
            key="tool.zz_weather",
            label="Weather",
            group=prompts.GROUP_TOOLS,
            order=500,
            default="- Check the forecast before answering about weather.",
            families=("zz_weather",),
        )
    )
    assert "tool.zz_weather" in prompts.catalogue(db)
    assert "Check the forecast" in _assembled(db, variables={}, families=("zz_weather",))
    assert any(
        fragment.key == "tool.zz_weather"
        for _, _, fragments in prompts.grouped(db)
        for fragment in fragments
    )
    # And it is editable through the same one write path as a built-in.
    prompts.save(db, {"tool.zz_weather": "- Ask the sky."})
    assert prompts.resolve(db, "tool.zz_weather") == "- Ask the sky."


def test_the_first_source_to_claim_a_key_keeps_it(db, extra_source):
    extra_source(
        prompts.Fragment(
            key="core.heading", label="x", group=prompts.GROUP_CORE, default="## Hijacked"
        )
    )
    assert prompts.catalogue(db)["core.heading"].default == "## How to work"


# --- The catalogue itself ----------------------------------------------------
def test_every_builtin_key_is_unique_and_well_formed():
    keys = [fragment.key for fragment in prompts.BUILTIN]
    assert len(keys) == len(set(keys))
    for key in keys:
        assert prompts.KEY_PATTERN.match(key), key


def test_every_variable_a_fragment_names_is_documented():
    """The legend is the only place a variable is explained, so a fragment
    referring to one that is not listed is a fragment nobody can use."""
    for fragment in prompts.BUILTIN:
        for name in (*fragment.variables, *fragment.requires):
            assert name in prompts.VARIABLE_NAMES, f"{fragment.key} names {name}"
        for name in prompts.variables_in(fragment.default):
            assert name in prompts.VARIABLE_NAMES, f"{fragment.key} uses {name}"


def test_every_variable_a_fragment_uses_is_declared():
    """Otherwise the field's own legend chips would not mention it."""
    for fragment in prompts.BUILTIN:
        for name in prompts.variables_in(fragment.default):
            assert name in fragment.variables, f"{fragment.key} uses undeclared {name}"
