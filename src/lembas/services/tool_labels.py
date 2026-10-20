"""What a tool is called in the interface, and what it looks like.

Four places have to agree about one tool, and for the whole life of the feature
they did not:

- the transcript (`chat/_tool_activity.html`) showed the machine's name for
  an agent tool -- "homeserver · ls -la", naming the machine rather than the
  thing that was done -- and the raw function name for everything else, so a
  saved memory read `memory_add`;
- the status line while a round runs said "Running shell_run…";
- the approval card had its own hand-written if-chain;
- and nothing checked that any of the three matched.

So the table lives here and each of them reads it.

`LABELS` and `ACTIONS` are deliberately different words for the same tool, the
same way `policy.MODE_HINTS` and `policy.MODE_GUIDANCE` are. A label is a noun
phrase in a list of things that happened; an approval card is a sentence
somebody is agreeing to, and "Bash" is not one.

**The precedence is inverted on purpose, and that is the whole design.**
Tool events are persisted in `Message.tool_calls_json`, so every row written
before today already carries `label: "homeserver"`. A resolver that preferred
the stored label would fix nothing for any transcript that already exists. So
a name this module knows about resolves from the static table and the stored
label is ignored; a name it does not know -- a custom HTTP tool, an MCP tool,
whose labels are per row and cannot be tabulated -- keeps its own. One rule,
both cases correct.

Resolved **without a database**. It is called once per rendered event, and
reaching for `tools.registry(db)` from a Jinja global would be two table scans
per bubble.
"""

from __future__ import annotations

from typing import Any

# What the transcript calls each tool. Kept in the same order as the families
# in `services/tools.py` so that adding one has an obvious home.
LABELS: dict[str, str] = {
    # Acting on the machine an agent chat is pointed at: the harness spec's names,
    # then the names they had before, which stored transcripts carry.
    "bash": "Bash",
    "read": "Read",
    "write": "Write",
    "edit": "Update",
    "multiedit": "Update",
    "apply_patch": "Patch",
    "list": "List",
    "glob": "Find files",
    "grep": "Search files",
    "todo": "Todo",
    "bash_output": "Job output",
    "bash_list": "Jobs",
    "bash_kill": "Job stopped",
    "shell_run": "Bash",
    "file_read": "Read",
    "file_write": "Write",
    "file_edit": "Update",
    "file_list": "List",
    "plan_submit": "Plan",
    "plan_update": "Plan updated",
    "plan_write": "Plan written",
    "task": "Helper",
    "job_output": "Job output",
    "job_list": "Jobs",
    "job_stop": "Job stopped",
    # The web.
    "web_search": "Web search",
    "web_fetch": "Fetch",
    "fetch": "Fetch",
    # The library.
    "knowledge_search": "Knowledge searched",
    "knowledge_get": "Document read",
    "notes_search": "Notes searched",
    "notes_get": "Note read",
    "notes_create": "Note written",
    "notes_edit": "Note updated",
    "notes_delete": "Note deleted",
    "scratch_write": "Canvas written",
    # Reports.
    "report_write": "Report filed",
    "report_search": "Reports searched",
    "report_get": "Report read",
    "image_generate": "Image",
    # Work set up to happen later.
    "schedule_create": "Scheduled",
    "schedule_list": "Schedules read",
    "schedule_update": "Schedule changed",
    "schedule_cancel": "Schedule stopped",
    # Work handed to a second model.
    "subagent_run": "Helper",
    # What a model keeps about the person it is talking to.
    "memory_add": "Memory saved",
    "memory_forget": "Memory removed",
    "skill_get": "Skill read",
    "skill_create": "Skill written",
    "skill_edit": "Skill updated",
    # Stopping to ask.
    "ask_user": "Asked you",
}

# A symbol id from templates/partials/icons.html. Everything used to be the
# sparkle, which said only "a model did something".
ICONS: dict[str, str] = {
    "bash": "terminal",
    "read": "file-text",
    "write": "pencil",
    "edit": "diff",
    "multiedit": "diff",
    "apply_patch": "diff",
    "list": "folder",
    "glob": "search",
    "grep": "search",
    "todo": "check",
    "bash_output": "clock",
    "bash_list": "dots",
    "bash_kill": "stop-circle",
    "shell_run": "terminal",
    "file_read": "file-text",
    "file_write": "pencil",
    "file_edit": "diff",
    "file_list": "folder",
    "plan_submit": "check",
    "plan_update": "check",
    "plan_write": "check",
    "task": "sparkle",
    "job_output": "clock",
    "job_list": "dots",
    "job_stop": "stop-circle",
    "web_search": "globe",
    "web_fetch": "link",
    "fetch": "link",
    "knowledge_search": "archive",
    "knowledge_get": "file-text",
    "notes_search": "search",
    "notes_get": "file-text",
    "notes_create": "pencil",
    "notes_edit": "pencil",
    "notes_delete": "trash",
    "scratch_write": "file-text",
    "report_write": "pencil",
    "report_search": "search",
    "report_get": "file-text",
    "image_generate": "image",
    "schedule_create": "clock",
    "schedule_list": "clock",
    "schedule_update": "clock",
    "schedule_cancel": "stop-circle",
    "subagent_run": "sparkle",
    "memory_add": "star",
    "memory_forget": "trash",
    "skill_get": "sparkle",
    "skill_create": "sparkle",
    "skill_edit": "sparkle",
    "ask_user": "chat",
}

# The icon for an event whose tool is not in the table -- a custom HTTP tool, an
# MCP tool, or a row written before `kind` existed.
KIND_ICONS: dict[str, str] = {
    "search": "globe",
    "fetch": "link",
    "custom": "link",
    "mcp": "server",
    "image": "image",
}
FALLBACK_ICON = "sparkle"

# What an approval card is headed. A sentence somebody agrees to, in the
# imperative, because that is what pressing the button does.
ACTIONS: dict[str, str] = {
    "bash": "Run a command",
    "read": "Read a file",
    "write": "Write a file",
    "edit": "Update a file",
    "multiedit": "Update a file",
    "apply_patch": "Patch files",
    "list": "List a directory",
    "glob": "Find files",
    "grep": "Search files",
    "bash_kill": "Stop a background job",
    "shell_run": "Run a command",
    "file_read": "Read a file",
    "file_write": "Write a file",
    "file_edit": "Update a file",
    "file_list": "List a directory",
    "web_search": "Search the web",
    "web_fetch": "Fetch a page",
    "fetch": "Fetch a page",
    "knowledge_search": "Search the library",
    "knowledge_get": "Read a document",
    "notes_search": "Search notes",
    "notes_get": "Read a note",
    "notes_create": "Write a note",
    "notes_edit": "Change a note",
    "notes_delete": "Delete a note",
    "scratch_write": "Write in the canvas",
    "report_write": "File a report",
    "report_search": "Search reports",
    "report_get": "Read a report",
    "image_generate": "Generate an image",
    "schedule_create": "Set up a schedule",
    "schedule_update": "Change a schedule",
    "schedule_cancel": "Stop a schedule",
    "subagent_run": "Send a helper",
    "task": "Send a helper",
    "memory_add": "Remember something",
    "memory_forget": "Forget something",
    "skill_get": "Read a skill",
    "skill_create": "Write a skill",
    "skill_edit": "Change a skill",
    "job_stop": "Stop a background job",
}

# Which argument is the thing being agreed to. Shown verbatim and escaped on the
# card: a summary that paraphrased it would be a card approving something other
# than what runs.
DETAIL_KEYS: dict[str, str] = {
    "bash": "command",
    "read": "path",
    "write": "path",
    "edit": "path",
    "multiedit": "path",
    "list": "path",
    "glob": "pattern",
    "grep": "pattern",
    "bash_kill": "id",
    # Not `apply_patch`: what it is agreed to is a set of files, which `describe`
    # names, and there is no one value on the card that could be corrected.
    "shell_run": "command",
    "file_read": "path",
    "file_write": "path",
    "file_edit": "path",
    "file_list": "path",
    "web_fetch": "url",
    "fetch": "url",
    "web_search": "query",
    "knowledge_search": "query",
    "notes_search": "query",
    "report_search": "query",
    # The title, not the body: a card has room for a line and the body is the
    # report. Editable for the same reason the image prompt is -- correcting
    # what a report will be called before it is filed is cheap, and renaming
    # one afterwards means finding it first.
    "report_write": "title",
    "job_stop": "id",
    # The thing being agreed to is what will be drawn, not which sampler draws
    # it. Also what makes the box on the card editable: a prompt corrected
    # before it runs is the commonest useful edit this feature will see.
    "image_generate": "prompt",
    # The instruction, not the timing. An approval card has room for one line,
    # and the instruction is the part a person can read and correct -- the
    # timing is an object, and the tool answers with it in words afterwards,
    # which is where it is actually checkable.
    "schedule_create": "instruction",
    # The task, not the title. It is the thing a helper is actually sent, and
    # the one field worth correcting before it goes -- a task with a wrong path
    # in it comes back as a confident answer about the wrong thing.
    "subagent_run": "task",
    "task": "prompt",
    # The question, not the model asked. It is what actually goes, and a
    # question carrying a wrong assumption comes back as a confident answer
    # about the wrong thing -- the same reason `subagent_run` names the task.
    # What the next round is for. The only field it has, and the one thing worth
    # correcting before several models spend a reply each on it.
    # The whole text, because for these two the text *is* the thing being agreed
    # to: there is no shorter field that says what the model would become.
}


def _name_of(event: dict[str, Any] | str) -> str:
    if isinstance(event, str):
        return event
    return str(event.get("name") or "")


def label_for(event: dict[str, Any] | str) -> str:
    """What to call this tool in the transcript.

    The static table wins over anything stored on the event. See the module
    docstring: rows already on disk carry the wrong label, and deferring to them
    would leave every existing transcript naming a machine.
    """
    name = _name_of(event)
    if name in LABELS:
        return LABELS[name]
    if isinstance(event, dict):
        stored = str(event.get("label") or "").strip()
        if stored:
            return stored
    return name


def icon_for(event: dict[str, Any] | str) -> str:
    """A symbol id for this event, never empty.

    Falls through the tool's own icon, then the event's `kind`, then the
    generic one -- so a custom tool still gets a link and an MCP tool a server,
    which is what the template used to decide for itself.
    """
    name = _name_of(event)
    if name in ICONS:
        return ICONS[name]
    kind = str(event.get("kind") or "") if isinstance(event, dict) else ""
    if not kind and name == "web_search":
        # Rows written before `kind` existed. The template made the same
        # allowance for the same reason.
        kind = "search"
    return KIND_ICONS.get(kind, FALLBACK_ICON)


def patch_files(text: str) -> list[str]:
    """The files a patch names, in either of `apply_patch`'s formats, as written."""
    import re

    found = re.findall(
        r"^\*\*\* (?:Add|Update|Delete) File: (.+)$|^\*\*\* Move to: (.+)$|^\+\+\+ (?:b/)?(\S.*)$",
        text or "",
        re.M,
    )
    names = [next(g for g in groups if g).strip() for groups in found]
    return [n for n in dict.fromkeys(names) if n != "/dev/null"]


def describe(name: str, args: dict[str, Any]) -> tuple[str, str]:
    """What an approval card says about one call: a title, and the detail."""
    if name == "apply_patch":
        files = patch_files(str(args.get("patch") or "")) or [str(args.get("path") or "")]
        return ACTIONS[name], ", ".join(f for f in files if f)
    title = ACTIONS.get(name)
    key = DETAIL_KEYS.get(name)
    if title is not None:
        return title, str(args.get(key) or "") if key else ""
    detail = ", ".join(f"{k}={v!r}" for k, v in list(args.items())[:4])
    return f"Use {label_for(name)}", detail[:400]


__all__ = [
    "ACTIONS",
    "DETAIL_KEYS",
    "FALLBACK_ICON",
    "ICONS",
    "KIND_ICONS",
    "LABELS",
    "describe",
    "icon_for",
    "patch_files",
    "label_for",
]
