"""What an import says when it is done: what it took, what it left, and why.

An import never fails whole over one item (`LLeMbas.wiki/Export-format.md`,
*Importing*): it names and drops. Both the import from `lembas.db.old`
(`services/import_old.py`) and the administrator's export file
(`services/import_admin.py`) answer with one of these, the page renders it
(`admin/import_report.html`) and the command line prints it.

The reasons are English sentences marked with `N_()` where they are raised and
translated where they are shown (`why()`), so the command line and the log say
the English and the page says the reader's language.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lembas.web.i18n import N_, t

# What an entry is, as the report names it.
KIND_LABELS = {
    "note": N_("Note"),
    "report": N_("Report"),
    "skill": N_("Skill"),
    "document": N_("Document"),
    "memory": N_("Memory"),
    "data_group": N_("Data group"),
    "connection": N_("Connection"),
    "model": N_("Model"),
    "settings": N_("Settings"),
    "image_workflow": N_("Image workflow"),
    "custom_tool": N_("Custom tool"),
    "mcp_server": N_("MCP server"),
    "secret": N_("Key"),
}


@dataclass
class Entry:
    kind: str
    name: str
    reason: str = ""
    fields: dict[str, object] = field(default_factory=dict)

    def label(self) -> str:
        text = KIND_LABELS.get(self.kind)
        return t(text) if text else self.kind

    def why(self) -> str:
        """The reason in the reader's language."""
        return t(self.reason, **self.fields) if self.reason else ""

    def why_english(self) -> str:
        return (self.reason % self.fields if self.fields else self.reason) if self.reason else ""


@dataclass
class Report:
    taken: list[Entry] = field(default_factory=list)
    skipped: list[Entry] = field(default_factory=list)
    # Secrets the file did not carry, or that did not open: keys to type in again.
    secrets: list[Entry] = field(default_factory=list)
    # A sentence or two about the whole run, as reasons are (N_ text, fields).
    notes: list[Entry] = field(default_factory=list)

    def take(self, kind: str, name: str, reason: str = "", **fields: object) -> None:
        self.taken.append(Entry(kind, name, reason, fields))

    def skip(self, kind: str, name: str, reason: str, **fields: object) -> None:
        self.skipped.append(Entry(kind, name, reason, fields))

    def secret(self, kind: str, name: str, reason: str, **fields: object) -> None:
        self.secrets.append(Entry(kind, name, reason, fields))

    def note(self, reason: str, **fields: object) -> None:
        self.notes.append(Entry("", "", reason, fields))

    def lines(self) -> list[str]:
        """The report as plain English lines, for the command line."""
        out = [f"Imported: {len(self.taken)}"]
        out += [f"  + {e.kind}: {e.name}" + (f" ({e.why_english()})" if e.reason else "")
                for e in self.taken]
        out.append(f"Skipped: {len(self.skipped)}")
        out += [f"  - {e.kind}: {e.name}: {e.why_english()}" for e in self.skipped]
        if self.secrets:
            out.append(f"Keys to enter again: {len(self.secrets)}")
            out += [f"  ! {e.kind}: {e.name}: {e.why_english()}" for e in self.secrets]
        out += [f"Note: {e.why_english()}" for e in self.notes]
        return out


__all__ = ["KIND_LABELS", "Entry", "Report"]
