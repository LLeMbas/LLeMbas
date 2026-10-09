"""Filling a ComfyUI template.

The thing worth pinning here is types. ComfyUI validates its inputs, so a
workflow whose `steps` arrives as the string "20" is refused -- and the refusal
happens on somebody's machine a minute later rather than in a test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import lembas
from lembas.services.images import workflow as wf

BASE = json.loads(
    (Path(lembas.__file__).parent / "services/images/base_workflow.json").read_text(
        encoding="utf-8"
    )
)


def _filled(**given):
    return wf.fill(BASE, wf.resolve({"prompt": "a bicycle", **given}))


# --- Substitution --------------------------------------------------------------
def test_a_whole_placeholder_keeps_its_type():
    """`"steps": "{{steps}}"` has to become the number 20, not the text "20".
    Substituting textually is the obvious implementation and produces a document
    ComfyUI refuses."""
    sampler = _filled(steps=25, cfg=7.5, seed=42)["3"]["inputs"]

    assert sampler["steps"] == 25
    assert isinstance(sampler["steps"], int)
    assert sampler["cfg"] == 7.5
    assert isinstance(sampler["cfg"], float)
    assert sampler["seed"] == 42
    assert isinstance(sampler["seed"], int)


def test_a_placeholder_inside_a_string_is_text():
    """Which is what makes `"{{prompt}}, masterpiece"` a usable template."""
    filled = wf.fill(
        {"n": {"inputs": {"text": "{{prompt}}, masterpiece"}}}, wf.resolve({"prompt": "a cat"})
    )
    assert filled["n"]["inputs"]["text"] == "a cat, masterpiece"


def test_the_prompt_and_the_negative_go_to_different_nodes():
    filled = _filled(negative="blurry")
    assert filled["6"]["inputs"]["text"] == "a bicycle"
    assert filled["7"]["inputs"]["text"] == "blurry"


def test_the_links_between_nodes_survive():
    """A workflow is a graph, and `["4", 0]` is an edge rather than a value. A
    filler that walked only dicts would flatten every one of them."""
    filled = _filled()
    assert filled["3"]["inputs"]["model"] == ["4", 0]
    assert filled["8"]["inputs"]["vae"] == ["4", 2]
    assert filled["5"]["inputs"]["batch_size"] == 1


def test_an_unknown_placeholder_is_left_alone():
    """`prompts.substitute`'s rule. A literal `{{x}}` is not a feature, but
    silently deleting one is worse than leaving it where somebody can see it."""
    filled = wf.fill({"n": {"inputs": {"lora": "{{lora_name}}"}}}, wf.resolve({"prompt": "x"}))
    assert filled["n"]["inputs"]["lora"] == "{{lora_name}}"


def test_the_shipped_template_uses_every_placeholder_it_should():
    assert wf.placeholders_in(BASE) == set(wf.PLACEHOLDERS)


# --- Resolution ----------------------------------------------------------------
def test_what_is_not_asked_for_takes_its_default():
    values = wf.resolve({"prompt": "a bicycle"})
    assert values["steps"] == 20
    assert values["cfg"] == 8.0
    assert values["width"] == 512
    assert values["negative"] == "text, watermark"
    assert values["sampler"] == "euler"


def test_the_seed_is_random_when_nobody_names_one():
    """A fixed default would make every unspecified generation identical -- and
    would make the retry loop produce the same rejected image four times."""
    seeds = {wf.resolve({"prompt": "x"})["seed"] for _ in range(8)}
    assert len(seeds) == 8


def test_a_named_seed_is_kept():
    assert wf.resolve({"prompt": "x", "seed": 1234})["seed"] == 1234


@pytest.mark.parametrize("sentinel", [-1, -5, "-1"])
def test_a_negative_seed_means_random(sentinel):
    """`-1` is what ComfyUI's own interface, A1111 and everything else that has
    ever asked for a seed use for "surprise me", so a model that has read any of
    them will write it.

    Without this it went through the uint64 wrap and came out as
    18446744073709551615 -- a perfectly valid *fixed* seed, so asking for
    something new produced the same picture every time.
    """
    seeds = {wf.resolve({"prompt": "x", "seed": sentinel})["seed"] for _ in range(8)}
    assert len(seeds) == 8
    assert all(0 <= seed <= wf.MAX_SEED for seed in seeds)


def test_seed_zero_is_a_real_seed():
    """It is the boundary the negative test could easily have swallowed, and
    zero is a seed somebody deliberately picks."""
    assert wf.resolve({"prompt": "x", "seed": 0})["seed"] == 0


@pytest.mark.parametrize("empty", [None, ""])
def test_null_and_empty_mean_no_opinion(empty):
    """A model that emits `"steps": null` rather than omitting the key is common
    enough that reading it as a request for zero steps would be a bug nobody
    could see."""
    assert wf.resolve({"prompt": "x", "steps": empty})["steps"] == 20


def test_out_of_range_numbers_are_clamped_rather_than_refused():
    """A model asking for 300 steps has misjudged rather than misbehaved, and a
    clarifying round costs more than doing the sensible thing."""
    values = wf.resolve({"prompt": "x", "steps": 5000, "cfg": -4, "denoise": 12})
    assert values["steps"] == 150
    assert values["cfg"] == 0.0
    assert values["denoise"] == 1.0


def test_nonsense_falls_back_instead_of_raising():
    """Arguments come from a model, so "twenty" is a thing that will arrive."""
    assert wf.resolve({"prompt": "x", "steps": "twenty"})["steps"] == 20


def test_a_seed_larger_than_comfyui_allows_is_wrapped():
    assert 0 <= wf.resolve({"prompt": "x", "seed": wf.MAX_SEED + 5})["seed"] <= wf.MAX_SEED


# --- Instance defaults: the rung that did not exist -----------------------------
def test_an_instance_default_beats_the_built_in_floor():
    """For the whole life of this feature there were two rungs, so 512x512 and
    twenty steps were what every instance got whatever card it was running on.
    The only ways to move them were to bake literals into a template instead of
    placeholders, or to write prose in the instructions box and hope."""
    values = wf.resolve({}, settings={"default_width": 1024, "default_steps": 30})

    assert values["width"] == 1024
    assert values["steps"] == 30
    # And everything unset still falls through.
    assert values["height"] == wf.DEFAULTS["height"]
    assert values["cfg"] == wf.DEFAULTS["cfg"]


def test_a_model_still_beats_an_instance_default():
    """Most specific wins, which is the same ladder the checkpoint and the
    workflow already follow."""
    values = wf.resolve({"width": 768}, settings={"default_width": 1024})
    assert values["width"] == 768


def test_an_empty_setting_is_no_opinion_rather_than_zero():
    """The empty string is how an administrator says "leave this alone". Read as
    a number it would set every instance to zero steps, and a zero-step
    generation is a refusal from ComfyUI that looks like a broken model."""
    values = wf.resolve({}, settings={"default_steps": "", "default_cfg": None})

    assert values["steps"] == wf.DEFAULTS["steps"]
    assert values["cfg"] == wf.DEFAULTS["cfg"]


def test_an_instance_default_is_clamped_like_any_other():
    """A number written straight into the settings row, or stored by an earlier
    version, still has to be safe when a generation reads it."""
    values = wf.resolve({}, settings={"default_steps": 9000, "default_width": 4})

    assert values["steps"] == wf.LIMITS["steps"][1]
    assert values["width"] == wf.LIMITS["width"][0]


def test_no_settings_at_all_behaves_exactly_as_before():
    """The whole safety of adding a rung: an instance that sets nothing is
    unchanged.

    Everything but the seed, which is deliberately fresh on every call — two
    resolves that agreed about it would mean the retry loop produced the same
    rejected image four times over.
    """
    without = wf.resolve({}, settings=None)
    plain = wf.resolve({})
    del without["seed"], plain["seed"]

    assert without == plain


# --- Batch ----------------------------------------------------------------------
def test_batch_fills_from_the_instance_and_not_from_the_model():
    """`batch_size` was a literal 1 in the base template, so an administrator
    whose card can comfortably make four at a time had no way of saying so short
    of editing JSON. It is deliberately not a tool parameter: a model asking for
    six because it is unsure is exactly the cost this must not invite."""
    assert "batch" not in wf.MODEL_SETTABLE
    assert wf.resolve({}, settings={"default_batch": 4})["batch"] == 4
    # A model that invents the key is ignored rather than refused.
    assert wf.resolve({"batch": 6}, settings={"default_batch": 2})["batch"] == 2


def test_the_base_template_takes_the_batch_placeholder():
    """A placeholder nothing references is a setting that silently does
    nothing."""
    assert "batch" in wf.placeholders_in(BASE)


def test_every_placeholder_is_described():
    """The editor's legend is built from this table, so a placeholder added
    without one appears in the list with a blank beside it."""
    for name in wf.PLACEHOLDERS:
        assert name in wf.DESCRIPTIONS, name
        assert wf.DESCRIPTIONS[name][1].strip(), name
