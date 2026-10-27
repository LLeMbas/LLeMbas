"""Instance-wide settings that administrators can change at runtime.

Distinct from ``lembas.config``, which holds deployment configuration read from
the environment at startup. Anything here is editable from the admin UI and
lives in the ``settings`` table.

Environment variables act as the *initial* value only. Once an administrator
sets something in the UI, the stored value wins -- otherwise a toggle in the
interface would silently revert on the next restart, which is worse than not
offering the toggle at all.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session as DBSession

from lembas.db.models import Setting

GENERAL = "general"
AUDIO = "audio"

# Used when nothing is stored: no ceiling. This module is where the setting is
# read, by the loop and the harness alike.
DEFAULT_CHAT_ROUNDS = 0
SEARCH = "search"
PROMPTS = "prompts"
AGENTS = "agents"
IMAGES = "images"
SCHEDULES = "schedules"
SUBAGENTS = "subagents"
EXTRACTION = "extraction"
UPDATES = "updates"


def _general_defaults() -> dict[str, Any]:
    return {
        # What model, speech and picture prices are typed in, and so what a
        # reply's cost is shown in (services/pricing.py).
        "currency": "USD",
        # What the interface is rendered in when a person has not chosen. Empty
        # and "en" mean the same thing; `web/i18n.known` is what decides, so a
        # value from a release that offered more languages than this one cannot
        # leave somebody with a page nobody can read.
        "language": "",
        # When on, new accounts land in the `pending` role and cannot sign in
        # until an administrator approves them. Reserved for the users pass.
        "require_approval": False,
        # What this instance is called: device logins and
        # `/.well-known/lembas.json` show it. Empty is "LLeMbas".
        "instance_name": "",
        # Custom instructions and a personality, chosen by each person for
        # themselves. Off until an administrator offers it.
        "personalization_enabled": False,
        # Applied to every chat that has no model or chat prompt of its
        # own. See services.chat.effective_system_prompt.
        "system_prompt": "",
        # Percentage of a model's context length at which the earlier turns are
        # summarised automatically. 0 turns it off; the Compact button still
        # works, because a person asking for it does not need a threshold.
        # Never fires for a model whose context_length is 0, since that is
        # "unknown" rather than "small". See services/compaction.py.
        "compact_threshold": 95,
        # How many rounds of tool calls an ordinary chat may take before it has
        # to answer with words. A **ceiling**, not a schedule: the loop already
        # ends the moment a round comes back with no tool calls, which is the
        # model saying it has what it needs. This only catches the case where it
        # never says so.
        #
        # Zero, meaning no ceiling, and the loop falls back to MAX_TOOL_ROUNDS
        # as a runaway backstop. It was 1, then 5, and both were the same
        # mistake at different scales: a number low enough to be reached by
        # ordinary work is not a ceiling, it is a schedule, and it overrides the
        # model's judgement on every turn rather than catching a runaway. Five
        # was reached by a small local model doing a genuinely good piece of
        # research -- six searches, each one informed by the last -- and the
        # reply ended there.
        #
        # What actually bounds an ordinary chat is the context window
        # (`CONTEXT_HEADROOM`), which is a real limit rather than a guess at how
        # much looking-up a question deserves. An administrator who wants a
        # ceiling can still set one, and `core.rounds` then tells the model it
        # has one; with none, `core.keep_working` tells it to work until done.
        "max_chat_rounds": 0,
        # What one reply may spend (harness spec `loop.json`, read by
        # `generation.loop_budgets`): seconds of work, bytes of tool output,
        # tokens written. None takes the spec's default for LLeMbas; 0 turns
        # one off. Instance settings without a form yet.
        "loop_wall_seconds": None,
        "loop_output_bytes": None,
        "loop_completion_tokens": None,
        # Prototypes: HTML a model writes and the reader's browser runs, sealed
        # off from this application (services/prototypes.py). On by default --
        # nothing runs here, and the permission and each chat's own switch
        # narrow it further.
        "prototypes_enabled": True,
        # Whether a prototype may load anything over https: a charting library
        # from a CDN, a web font, a picture. Off by default for the reason hard
        # rule 2 exists -- a page that loads a script from a third party tells
        # that party somebody opened it, and does not work offline.
        "prototypes_network": False,
        # How long a person's temporary chat lives after its last message, in
        # minutes; 0 means until they leave it. Temporary chats read and write
        # nothing that outlives them (services/chat.py:read_only_temporary), so
        # this is the whole of how long one leaves a trace. An hour is closer
        # to what "temporary" promises than a day.
        "temporary_lifetime_minutes": 60,
        # Requests a minute one API token may make (security/api_tokens.py). A
        # guard against a loop gone wrong, not a quota; 0 turns it off.
        "api_requests_per_minute": 120,
        # Days the audit log keeps an event (services/audit.py); 0 keeps them for
        # ever.
        "audit_retention_days": 365,
        # "full", or "api": the instance is an API front only -- `/v1`,
        # `/api/v1`, device login and the link keep working; chats, the library,
        # reports, schedules and `/mcp` are switched off by one gate,
        # `services/instance_mode.py`. See `api_only` below.
        "instance_mode": "full",
    }


def _agents_defaults() -> dict[str, Any]:
    """Agentic execution: running commands on a linked device.

    Deliberately never on the machine LLeMbas runs on. Executing here would put
    the blast radius on the host holding the database and the encryption key,
    and buying it back needs a sandbox, a bind list, a second unix account and
    an argument about every one of them. An agent chat runs on a device somebody
    linked -- the LLeMbas CLI on their own machine, which decides for itself
    what it allows and where.
    """
    return {
        # Off until an administrator turns it on. Not caution for its own sake:
        # a model reads web pages, files and command output, all of them
        # untrusted, so a shell is a capability somebody chooses on purpose.
        "enabled": False,
        # How long a reply waits for someone to answer. Clamped on read: a zero
        # here would park a background task forever.
        "approval_timeout": 900,
        "ask_free_text": True,
        # The terminal panel: a person's own shell on their own device.
        # Separate from `enabled` because the two are different capabilities --
        # one lets a model run commands, the other lets a human do what they
        # could already do in the device's own terminal. Neither implies the other.
        "terminal_enabled": True,
        # Seconds with nobody watching *and* nothing typed before the session is
        # closed. A build running with the panel shut is not idle. Clamped on
        # read: zero would leave a shell open until the next restart.
        "terminal_idle_timeout": 1800,
        # Open shells across the instance, and per person. Each is a PTY held open
        # on a device and fed over its link, so this is a real resource, not a scruple.
        "terminal_max_sessions": 20,
        "terminal_max_per_user": 3,
    }


def _audio_defaults() -> dict[str, Any]:
    """Speech-to-text and text-to-speech endpoints.

    Two separate endpoints rather than one, because they usually are: a local
    install runs whisper.cpp for one and Kokoro for the other. Both speak the
    OpenAI audio API, so the shape below is the same on each side.
    """
    return {
        "stt_enabled": False,
        "stt_base_url": "",
        "stt_api_key_encrypted": "",
        "stt_model": "whisper-1",
        # Empty means "let the server detect it", which is what whisper does
        # best. A forced language is an override, not a default.
        "stt_language": "",
        "tts_enabled": False,
        "tts_base_url": "",
        "tts_api_key_encrypted": "",
        "tts_model": "tts-1",
        "tts_voice": "",
        "tts_format": "mp3",
        "tts_speed": 1.0,
        # The instance-wide starting point for the per-user toggle, not a
        # setting that forces anything on anyone.
        "tts_autoplay": False,
        # What speech costs, in money, like a
        # model's prices: per minute of recording transcribed, and per million
        # characters read aloud -- the units the speech services themselves
        # bill in. 0 is free, which is what a local whisper.cpp or Kokoro is.
        "stt_per_minute": 0.0,
        "tts_per_1m_chars": 0.0,
    }


def _search_defaults() -> dict[str, Any]:
    return {
        "enabled": False,
        "provider": "ddgs",
        "max_results": 5,
        "region": "wt-wt",
        "safesearch": "moderate",
        "searxng_base_url": "",
        "firecrawl_base_url": "https://api.firecrawl.dev",
        "firecrawl_api_key_encrypted": "",
        "timeout": 20.0,
        # Whether saving a link may reach addresses on this machine or this
        # network. Off, because a server that fetches any URL it is handed can
        # be pointed at a router's admin page or at LLeMbas itself, and the URL
        # can come from a model. See services/fetch.py.
        "allow_private_fetch": False,
        # Whether a *model* may ask for a page itself. Separate from the switch
        # above, and separate from web search: attaching a link is a person's
        # instruction, while this is a model choosing an address -- possibly one
        # it read in a page it just fetched.
        "fetch_enabled": True,
    }


def _prompts_defaults() -> dict[str, Any]:
    """Deliberately carries no prompt text.

    The default wording of every fragment lives in ``services/prompts.py``, and
    only an administrator's *override* is stored here. That is what lets a later
    release improve a default and have the improvement reach every instance that
    never touched that fragment -- copying the defaults in here at first save
    would freeze them forever.
    """
    return {
        # 0 means "use services.harness.MAX_HARNESS_CHARS".
        "max_harness_chars": 0,
    }


def _images_defaults() -> dict[str, Any]:
    """Generating pictures on a ComfyUI somebody else is running."""
    return {
        "enabled": False,
        "base_url": "",
        "api_key_encrypted": "",
        # A generation is tens of seconds and a queue in front of it can be
        # minutes. Far longer than any other timeout here, because the thing
        # being waited for genuinely takes that long.
        "timeout": 600.0,
        # What this ComfyUI advertises, discovered by the Test button and stored
        # so the request path never has to ask. The checkpoints are also the
        # enum a model chooses from, which is why an empty list means the tool
        # is not offered: a model naming a checkpoint that does not exist gets a
        # refusal from ComfyUI and spends a round finding out.
        "checkpoints": [],
        "samplers": [],
        "schedulers": [],
        "default_workflow_id": "",
        # What a generation uses when nothing names otherwise. Empty means "no
        # opinion" for every one of them, falling through to
        # `workflow.DEFAULTS` -- which is why they are empty here rather than
        # holding a copy of that dict. A copy would freeze an instance on
        # whatever this file said the day it was installed, and would make
        # improving a floor in code reach nobody.
        #
        # These exist because for the whole life of this feature there were
        # none: 512x512, euler and twenty steps were what every instance got
        # whatever card it was running on, and the only ways to move them were
        # to bake literals into a template instead of placeholders, or to write
        # prose in the instructions box and hope.
        "default_checkpoint": "",
        "default_steps": "",
        "default_cfg": "",
        "default_width": "",
        "default_height": "",
        "default_sampler": "",
        "default_scheduler": "",
        "default_denoise": "",
        "default_negative": "",
        # How many pictures one run makes. Not offered to the model at all --
        # see workflow.MODEL_SETTABLE -- because a model asking for six because
        # it is unsure is exactly the cost this must not invite.
        "default_batch": "",
        # Whether a vision model looks at what came back and says whether to
        # keep it. Deliberately independent of `preserve_vram` below: on a
        # machine that can hold both models this costs nothing, and on one that
        # cannot it costs two model loads per retry, which is a judgement only
        # the person running it can make.
        "review_enabled": False,
        # Which model judges. Empty means the chat's own, when it has vision.
        "review_model_id": "",
        "max_tries": 4,
        # Unload the chat's own LLM while ComfyUI works, and free ComfyUI
        # afterwards. For a machine that cannot hold both at once. Off by
        # default: it makes every generation slower, and most people have the
        # memory.
        "preserve_vram": False,
        # Instance-wide guidance, injected into the harness beside the tool's
        # own. Where "always add these words to the negative prompt" lives.
        "instructions": "",
        # What one picture costs, in money; 0 is
        # free. Per image kept, whether a chat's tool or the API asked for it.
        "per_image": 0.0,
        # The most pictures one `POST /v1/images/generations` may ask for.
        # Each is a full generation on somebody's GPU, so `n` is
        # capped here rather than trusted.
        "api_max_images": 4,
    }


def _schedules_defaults() -> dict[str, Any]:
    """Scheduling: work that happens because time passed rather than because
    somebody asked just now.

    Off until an administrator turns it on, for the reason agent execution is:
    this spends model time — and, in an agent chat, runs commands — with nobody
    at the keyboard, which is a capability somebody chooses on purpose rather
    than one that arrives with an upgrade.
    """
    return {
        "enabled": False,
        # How often the ticker looks. The rule's own granularity is a minute, so
        # this bounds how late a firing can be; 30s costs one indexed SELECT.
        "tick_seconds": 30,
        # A ceiling per person, so one account cannot fill the ticker's sweep.
        "max_per_user": 20,
        # Firings running at once. Fifty schedules due at 09:00 must not open
        # fifty generations against one endpoint.
        "max_concurrent": 3,
        # The floor `rule.validate` clamps an interval up to. Separate from the
        # rule module's own hard minimum: an administrator may want a coarser
        # floor than "a minute" without editing code.
        "min_interval_seconds": 60,
        # How many turns may pile up unanswered in one chat before a firing is
        # skipped instead of queued. `_drain` takes one per reply, so an
        # unbounded queue is a backlog that outlives the day that caused it.
        "max_queued": 3,
    }


def _subagents_defaults() -> dict[str, Any]:
    """Delegating a piece of a reply to a second, unattended model.

    Off until an administrator turns it on, for the reason agent execution and
    scheduling are: a reply that may spawn helpers spends model time
    multiplicatively, and on a single local endpoint four of them at once is
    four times the queue rather than four times the speed.

    Every number below is a **ceiling on one reply's helpers**, not a working
    budget for one of them. The distinction is the one `chat_rounds` makes: a
    bound low enough to be reached by ordinary work stops the work
    halfway instead of catching a runaway.
    """
    return {
        "enabled": False,
        # How many one reply may spawn in total. Small on purpose: fanning out
        # across four sub-questions is the use this exists for, and a reply that
        # wants twenty has misunderstood the tool rather than found a use for it.
        "max_per_reply": 4,
        # Running at once across the whole instance. A subagent is a whole
        # generation against the same endpoint the parent is waiting on.
        "max_concurrent": 6,
        # What one subagent may spend. Its own numbers rather than the chat's or
        # the agent settings', because a helper answering one question is not
        # the same shape of work as the reply that asked it: it should run out
        # of room long before the parent does.
        "max_rounds": 30,
        "wall_seconds": 600,
        "max_completion_tokens": 60_000,
        # Whether the helper's own chat is kept after its answer is handed back.
        # Off means it is deleted, which is what makes this cheap to use; on is
        # for working out why one came back with something odd. Kept chats are
        # temporary either way, so the day-old sweep still gets them.
        "keep_transcript": False,
    }


_DEFAULTS: dict[str, Any] = {
    GENERAL: _general_defaults,
    AUDIO: _audio_defaults,
    SEARCH: _search_defaults,
    PROMPTS: _prompts_defaults,
    AGENTS: _agents_defaults,
    IMAGES: _images_defaults,
    SCHEDULES: _schedules_defaults,
    SUBAGENTS: _subagents_defaults,
    # Lambdas: both factories are defined below this table, which is where the
    # accessor that reads each group lives.
    EXTRACTION: lambda: _extraction_defaults(),
    UPDATES: lambda: _updates_defaults(),
}


def _updates_defaults() -> dict[str, Any]:
    """Updates that wait for an idle instance (services/updates_auto.py).
    Off by default: nothing is checked or applied until an administrator says."""
    return {
        "auto_apply": "off",
        "check_every_hours": 6,
        # "02:00-05:00", in the server's time; empty is any time.
        "window": "",
        "idle_minutes": 10,
        "drain_after_minutes": 60,
    }


def _extraction_defaults() -> dict[str, Any]:
    """What happens to a file between the upload and the model.

    The numbers were constants in `services/files.py` and every one of them is a
    trade somebody with a different corpus makes differently: a 20 MB ceiling is
    generous for notes and small for scans, and 120,000 characters is thirty
    thousand tokens, which is most of a small window and a rounding error in a
    large one. The defaults here are exactly the constants they replace, so an
    instance that changes nothing behaves as it always did.

    `Image.MAX_IMAGE_PIXELS` is deliberately **not** here. It is a
    decompression-bomb guard, not a preference: a 60,000x60,000 PNG is a few KB
    on disk and hundreds of gigabytes decoded, and nobody should be able to
    raise that from a form.
    """
    return {
        "max_upload_mb": 20,
        "max_image_edge": 1400,
        "jpeg_quality": 85,
        "max_pdf_pages": 300,
        "max_extracted_chars": 120_000,
        "orphan_hours": 24,
        # Extensions treated as text beyond the built-in list. Decodability is
        # what actually decides, so this only picks a media type -- which is why
        # it is a list of extensions rather than a mapping somebody has to get
        # right twice.
        "extra_text_extensions": [],
        # Whether a PDF nothing could read is stored with its error, or refused.
        # Keeping it is the default and the honest one: a scanned page is a file
        # somebody still wants attached, and the error says why it contributes
        # nothing rather than leaving them to wonder.
        "reject_unreadable_pdf": False,
        # --- Semantic search ---------------------------------------------------
        # Which model turns text into vectors. Empty means none, and none means
        # the keyword search that has always been here, byte for byte -- which
        # is what makes this safe to add to an instance that never asked for it.
        "embedding_model_id": "",
        # How long a chunk is, in characters, and how much of the previous one
        # rides along with it. Characters rather than tokens because the count
        # has to be made without asking the endpoint, and the estimate is the
        # same four-to-one this codebase already uses.
        "chunk_chars": 1200,
        "chunk_overlap": 150,
        # How many chunks one embedding request carries. Small enough that a
        # local endpoint is not asked for a megabyte at once.
        "embed_batch": 16,
    }


def extraction(db: DBSession) -> dict[str, Any]:
    """Extraction settings, clamped on read for the reason `agents` gives.

    Every floor here is a number that means something bad at zero: a zero-page
    PDF limit extracts nothing from every PDF and reports success, and a
    zero-character chunk is an infinite loop in the splitter.
    """
    values = get_group(db, EXTRACTION)
    values["max_upload_mb"] = min(max(int(values.get("max_upload_mb") or 1), 1), 512)
    values["max_image_edge"] = min(max(int(values.get("max_image_edge") or 1), 128), 8192)
    values["jpeg_quality"] = min(max(int(values.get("jpeg_quality") or 1), 30), 100)
    values["max_pdf_pages"] = min(max(int(values.get("max_pdf_pages") or 1), 1), 5000)
    values["max_extracted_chars"] = min(
        max(int(values.get("max_extracted_chars") or 1), 1000), 5_000_000
    )
    values["orphan_hours"] = min(max(int(values.get("orphan_hours") or 1), 1), 8760)
    values["chunk_chars"] = min(max(int(values.get("chunk_chars") or 1), 200), 8000)
    # Bounded *against the chunk*, not absolutely: an overlap at or past the
    # chunk size means every chunk starts where the last one did, which is a
    # splitter that never advances.
    values["chunk_overlap"] = min(
        max(int(values.get("chunk_overlap") or 0), 0), values["chunk_chars"] // 2
    )
    values["embed_batch"] = min(max(int(values.get("embed_batch") or 1), 1), 256)
    stored = values.get("extra_text_extensions")
    values["extra_text_extensions"] = (
        [str(item) for item in stored] if isinstance(stored, list) else []
    )
    return values


def defaults(key: str = GENERAL) -> dict[str, Any]:
    """The built-in values for a settings group, with nothing stored applied."""
    factory = _DEFAULTS.get(key)
    return factory() if factory else {}


def get_group(db: DBSession, key: str = GENERAL) -> dict[str, Any]:
    """Stored settings for a group, with defaults filled in for absent keys."""
    values = defaults(key)
    row = db.get(Setting, key)
    if row is not None and isinstance(row.value, dict):
        values.update(row.value)
    return values


def get(db: DBSession, name: str, *, key: str = GENERAL) -> Any:
    return get_group(db, key).get(name)


def chat_rounds(db: DBSession) -> int:
    """The ceiling on an ordinary chat's rounds of tool calls, clamped.

    Zero is meaningful and is not clamped away: it means "no ceiling", the same
    convention `max_completion_tokens` uses. Read through here
    rather than from the group directly so the loop and the harness cannot
    disagree about the number the model is told.
    """
    stored = get_group(db, GENERAL).get("max_chat_rounds")
    if stored is None:
        return DEFAULT_CHAT_ROUNDS
    return min(max(int(stored), 0), 100)


def update(db: DBSession, changes: dict[str, Any], *, key: str = GENERAL) -> dict[str, Any]:
    """Merge changes into a settings group and persist them."""
    row = db.get(Setting, key)
    if row is None:
        row = Setting(key=key, value={})
        db.add(row)

    # Reassigned rather than mutated: SQLAlchemy only reliably detects a change
    # to a JSON column when the whole value is replaced.
    row.value = {**(row.value or {}), **changes}
    db.commit()
    return get_group(db, key)


def replace(db: DBSession, values: dict[str, Any], *, key: str = GENERAL) -> dict[str, Any]:
    """Set a settings group to exactly these values, dropping anything absent.

    `update` merges, which is right for a form that posts a fixed set of fields
    and wrong for one whose fields come and go -- the prompt editor stores only
    the fragments an administrator has actually changed, so "no longer present"
    has to mean "no longer stored". There is no other way to delete a key.
    """
    row = db.get(Setting, key)
    if row is None:
        row = Setting(key=key, value={})
        db.add(row)

    row.value = dict(values)
    db.commit()
    return get_group(db, key)


INSTANCE_MODES = ("full", "api")


def instance_mode(db: DBSession) -> str:
    """"full" or "api". Reads the one `general` row and nothing else, because the
    gate in `services/instance_mode.py` asks on every chat or library request.
    Anything stored that is not a known mode reads as "full" -- what an instance
    was before the setting existed."""
    row = db.get(Setting, GENERAL)
    value = row.value if row is not None and isinstance(row.value, dict) else {}
    stored = value.get("instance_mode")
    return stored if stored in INSTANCE_MODES else "full"


def api_only(db: DBSession) -> bool:
    """Whether this instance serves the API only: no chats, agent chats,
    library, reports, schedules or `/mcp`, and nothing in the background starts a
    reply."""
    return instance_mode(db) == "api"


def audio(db: DBSession) -> dict[str, Any]:
    values = get_group(db, AUDIO)
    # The two prices, never negative and never junk.
    for name in ("stt_per_minute", "tts_per_1m_chars"):
        values[name] = money_amount(values.get(name))
    return values


def search(db: DBSession) -> dict[str, Any]:
    return get_group(db, SEARCH)


def agents(db: DBSession) -> dict[str, Any]:
    """Agent settings, with the numbers that must not be zero clamped.

    `approval_timeout` of 0 would park a background task on a question nobody
    is going to answer, and nothing else prunes a generation that is not
    finished. `terminal_idle_timeout` of 0 would keep a PTY on a
    device open until the next restart. Clamped on read rather than on save,
    so a value already stored by an earlier version cannot bite either.
    """
    values = get_group(db, AGENTS)
    values["approval_timeout"] = min(max(int(values.get("approval_timeout") or 0), 60), 3600)
    values["terminal_idle_timeout"] = min(
        max(int(values.get("terminal_idle_timeout") or 0), 60), 86400
    )
    values["terminal_max_sessions"] = min(
        max(int(values.get("terminal_max_sessions") or 0), 1), 500
    )
    values["terminal_max_per_user"] = min(max(int(values.get("terminal_max_per_user") or 0), 1), 50)
    return values


def images(db: DBSession) -> dict[str, Any]:
    """Image generation settings, with the numbers clamped.

    Clamped on read rather than on save, for the reason `agents` gives: a value
    stored by an earlier version cannot bite either. `max_tries` has a floor of
    one because zero would mean the tool generates nothing at all and reports
    success -- there is no reading of "no tries" that anybody wants, unlike the
    zeroes above, which each mean something.
    """
    values = get_group(db, IMAGES)
    values["timeout"] = min(max(float(values.get("timeout") or 0), 10.0), 3600.0)
    values["max_tries"] = min(max(int(values.get("max_tries") or 1), 1), 10)
    values["per_image"] = money_amount(values.get("per_image"))
    try:
        values["api_max_images"] = min(max(int(values.get("api_max_images") or 1), 1), 10)
    except (TypeError, ValueError):
        values["api_max_images"] = 4
    for name in ("checkpoints", "samplers", "schedulers"):
        stored = values.get(name)
        values[name] = [str(item) for item in stored] if isinstance(stored, list) else []
    return values


def schedules(db: DBSession) -> dict[str, Any]:
    """Scheduling settings, with the numbers clamped on read.

    Clamped here rather than at the save, for the reason `agents` gives: a value
    stored by an earlier version cannot bite either. Every floor below is a
    number that means something bad at zero -- a tick of 0 is a busy loop, a
    concurrency of 0 is a ticker that claims firings and never runs them, and
    both would look from the outside like scheduling simply not working.
    """
    values = get_group(db, SCHEDULES)
    values["tick_seconds"] = min(max(int(values.get("tick_seconds") or 30), 5), 300)
    values["max_per_user"] = min(max(int(values.get("max_per_user") or 20), 1), 200)
    values["max_concurrent"] = min(max(int(values.get("max_concurrent") or 3), 1), 20)
    values["min_interval_seconds"] = min(
        max(int(values.get("min_interval_seconds") or 60), 60), 86400
    )
    values["max_queued"] = min(max(int(values.get("max_queued") or 3), 1), 50)
    return values


def subagents(db: DBSession) -> dict[str, Any]:
    """Subagent settings, clamped on read for the reason `agents` gives.

    Zero is meaningful for `max_completion_tokens` alone — no ceiling on what
    one helper writes — and is a floor of one everywhere else, because a
    `max_per_reply` of zero is the feature switched off wearing the switch's
    clothes, and that is a thing to answer in one place rather than two.
    """
    values = get_group(db, SUBAGENTS)
    values["max_per_reply"] = min(max(int(values.get("max_per_reply") or 1), 1), 20)
    values["max_concurrent"] = min(max(int(values.get("max_concurrent") or 1), 1), 50)
    values["max_rounds"] = min(max(int(values.get("max_rounds") or 1), 1), 200)
    values["wall_seconds"] = min(max(int(values.get("wall_seconds") or 1), 30), 7200)
    values["max_completion_tokens"] = min(
        max(int(values.get("max_completion_tokens") or 0), 0), 5_000_000
    )
    return values


def images_ready(db: DBSession) -> bool:
    """Whether image generation can actually happen.

    Three things, and the checkpoint list is the one worth stating: without it a
    model has nothing to name, and ComfyUI refuses a workflow whose checkpoint
    does not exist -- so offering the tool would be offering a round that ends
    in a refusal. Read by the tool gate, which is why it lives here beside the
    values rather than in `tools.py` with the other gates.
    """
    values = images(db)
    return bool(values["enabled"] and values["base_url"] and values["checkpoints"])


# The currencies prices are typed in, and their signs.
CURRENCIES = {"EUR": "€", "USD": "$", "GBP": "£"}
DEFAULT_CURRENCY = "USD"


def money_amount(value: Any, default: float = 0.0) -> float:
    """A price or a money value from a form or a stored row: a number, never
    below zero; junk is the default."""
    try:
        number = float(str(value).strip().replace(",", ".")) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default
    return number if number >= 0 and number == number and number < 1e12 else default


def currency(db: DBSession) -> str:
    """The currency model prices are in, and so what a reply's cost is in
    (services/pricing.py). One of `CURRENCIES`; anything else reads as USD."""
    value = str(get(db, "currency") or "").upper()
    return value if value in CURRENCIES else DEFAULT_CURRENCY


def updates(db: DBSession) -> dict[str, Any]:
    """Update settings, merged over the defaults."""
    return {**_updates_defaults(), **(get_group(db, UPDATES) or {})}
