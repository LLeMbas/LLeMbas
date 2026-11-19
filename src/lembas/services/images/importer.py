"""Importing a workflow somebody built in ComfyUI: propose where the holes go.

ComfyUI exports two documents. The **API format** is what `/prompt` takes: an
object of nodes by id, each `{"class_type": ..., "inputs": {...}}`, where an
input is a literal or a link `[node id, output index]`. The **UI format**
(`nodes`, `links`, positions) is what its *Save* writes; it cannot be run, and
is recognised here only to be refused with how to get the other.

`propose` reads an API-format document and answers what it would change:
which literal becomes which `{{placeholder}}`, and what kind of thing the
workflow makes. **It changes nothing itself.** The page shows the proposal,
an administrator ticks what they agree with, and `apply` makes exactly those
replacements -- a guess by node class is a suggestion, and a workflow is
somebody's work.

How the guess is made:

- a sampler (`KSampler`, `KSamplerAdvanced`) gives seed, steps, cfg, sampler,
  scheduler, denoise -- and its `positive` and `negative` links are followed,
  through any node that passes conditioning on, to the text encoders: the one
  feeding `positive` is the prompt, the one feeding `negative` the negative.
  That is why the two are not told apart by order or by title;
- the split samplers (`RandomNoise`, `BasicScheduler`, `KSamplerSelect`,
  `CFGGuider`) give the same values where a workflow uses those;
- a latent-size node (`Empty…`, or one with width, height and a length)
  gives width, height, frames; a music latent gives seconds;
- `LoadImage` gives the input picture (the second one `image2`), a mask
  loader `mask`; a checkpoint loader `model` -- for a picture workflow only,
  whose checkpoints an administrator lists; any other kind keeps the file
  names it was built with;
- the save nodes say the kind.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any

from lembas.services.images import workflow

MAX_BYTES = 2 * 1024 * 1024
MAX_NODES = 2000

UI_FORMAT = (
    "This is ComfyUI's interface format (it has `nodes` and `links`), which cannot be "
    "run. In ComfyUI, use Workflow › Export (API) -- in older versions, turn on the "
    "developer mode options in its settings and use Save (API Format) -- and import "
    "that file."
)
NOT_A_WORKFLOW = (
    "This is not a ComfyUI workflow in API format: it should be an object of nodes, "
    'each with a "class_type" and "inputs".'
)

SAMPLER_INPUTS = {
    "seed": "seed", "noise_seed": "seed", "steps": "steps", "cfg": "cfg",
    "sampler_name": "sampler", "scheduler": "scheduler", "denoise": "denoise",
}
# The split samplers: class -> {input: placeholder}.
SPLIT = {
    "RandomNoise": {"noise_seed": "seed"},
    "BasicScheduler": {"steps": "steps", "scheduler": "scheduler", "denoise": "denoise"},
    "KSamplerSelect": {"sampler_name": "sampler"},
    "CFGGuider": {"cfg": "cfg"},
}
# The save nodes that say what a workflow makes, most telling first.
SAVES = (
    ("video", ("SaveVideo", "SaveWEBM", "SaveAnimatedWEBP", "SaveAnimatedPNG",
               "VHS_VideoCombine")),
    ("music", ("SaveAudio", "SaveAudioMP3", "SaveAudioOpus", "SaveAudioAdvanced")),
    ("model3d", ("SaveGLB", "Save3D", "Hy3DExportMesh")),
)
TEXT_INPUTS = ("text", "prompt")
FOLLOW_DEPTH = 8


@dataclass(frozen=True)
class Change:
    """One proposed replacement: this node's input becomes that placeholder."""

    node: str
    class_type: str
    input: str
    was: Any
    placeholder: str

    @property
    def key(self) -> str:
        return f"{self.node}:{self.input}"


@dataclass
class Proposal:
    kind: str = "image"
    changes: list[Change] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    nodes: int = 0


class ImportError_(ValueError):
    """Why a document cannot be imported, in a sentence for the page."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def parse(text: str | bytes) -> dict[str, Any]:
    """The pasted or uploaded document as a workflow, or the reason it is not
    one. A workflow wrapped as `{"prompt": {...}}` (what `/prompt` is sent) is
    unwrapped."""
    raw = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    if len(raw) > MAX_BYTES:
        raise ImportError_("This file is larger than a workflow is.")
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ImportError_(f"This is not JSON: {exc}") from exc
    except RecursionError as exc:
        # Brackets nested thousands deep: within the size cap, and still not
        # something to answer with a server error.
        raise ImportError_("This is not a workflow: it is nested too deeply.") from exc
    if isinstance(doc, dict) and isinstance(doc.get("nodes"), list) and "links" in doc:
        raise ImportError_(UI_FORMAT)
    if isinstance(doc, dict) and isinstance(doc.get("prompt"), dict) and "class_type" not in doc:
        inner = doc["prompt"]
        if inner and all(isinstance(n, dict) and "class_type" in n for n in inner.values()):
            doc = inner
    if (
        not isinstance(doc, dict)
        or not doc
        or len(doc) > MAX_NODES
        or not all(
            isinstance(node, dict)
            and isinstance(node.get("class_type"), str)
            and isinstance(node.get("inputs", {}), dict)
            for node in doc.values()
        )
    ):
        raise ImportError_(NOT_A_WORKFLOW)
    return doc


def _link(value: Any) -> str | None:
    """The node id an input is linked to, or None for a literal."""
    if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int):
        return str(value[0])
    return None


def _literal(value: Any) -> bool:
    return isinstance(value, str | int | float) and not isinstance(value, bool)


def _encoders(doc: dict[str, Any], start: str | None, role: str) -> list[str]:
    """The text-encoding nodes a sampler's `positive` or `negative` reaches."""
    found: list[str] = []
    seen: set[str] = set()
    frontier = [start] if start else []
    for _ in range(FOLLOW_DEPTH):
        nxt: list[str] = []
        for node_id in frontier:
            node = doc.get(node_id)
            if node_id in seen or not isinstance(node, dict):
                continue
            seen.add(node_id)
            inputs = node.get("inputs") or {}
            if any(_literal(inputs.get(name)) for name in (*TEXT_INPUTS, "tags")):
                found.append(node_id)
                continue
            # A node that passes conditioning on: the input of the same role
            # where it has one, else whatever conditioning it takes.
            names = [n for n in (role, "conditioning", "conditioning_1") if n in inputs]
            for name in names or [n for n in inputs if "cond" in n.lower()]:
                if (linked := _link(inputs.get(name))) is not None:
                    nxt.append(linked)
        frontier = nxt
    return found


def kind_of(doc: dict[str, Any]) -> str:
    classes = {str(node.get("class_type")) for node in doc.values()}
    for kind, names in SAVES:
        if classes & set(names):
            return kind
    loads = [c for c in classes if c in ("LoadImage", "LoadImageMask", "LoadImageOutput")]
    return "image_edit" if loads else "image"


def propose(doc: dict[str, Any]) -> Proposal:
    out = Proposal(kind=kind_of(doc), nodes=len(doc), files=workflow.model_files(doc))
    taken: set[str] = set()

    def add(node_id: str, name: str, placeholder: str) -> None:
        node = doc[node_id]
        value = (node.get("inputs") or {}).get(name)
        key = f"{node_id}:{name}"
        if key in taken or not _literal(value):
            return
        if isinstance(value, str) and workflow.placeholders_in(value):
            return  # already a hole
        taken.add(key)
        out.changes.append(Change(node_id, str(node["class_type"]), name, value, placeholder))

    ordered = sorted(doc, key=lambda k: (len(str(k)), str(k)))
    samplers = [k for k in ordered if str(doc[k]["class_type"]).startswith("KSampler")
                and str(doc[k]["class_type"]) != "KSamplerSelect"]
    for node_id in samplers:
        inputs = doc[node_id].get("inputs") or {}
        for name, placeholder in SAMPLER_INPUTS.items():
            if name in inputs:
                add(node_id, name, placeholder)
        for role, placeholder in (("positive", "prompt"), ("negative", "negative")):
            for encoder in _encoders(doc, _link(inputs.get(role)), role):
                enc_inputs = doc[encoder].get("inputs") or {}
                if "tags" in enc_inputs and role == "positive":
                    add(encoder, "tags", "tags")
                    add(encoder, "lyrics", "lyrics")
                    continue
                for name in TEXT_INPUTS:
                    if name in enc_inputs:
                        add(encoder, name, placeholder)
    for node_id in ordered:
        node = doc[node_id]
        cls = str(node["class_type"])
        inputs = node.get("inputs") or {}
        for name, placeholder in SPLIT.get(cls, {}).items():
            add(node_id, name, placeholder)
        if "width" in inputs and "height" in inputs and (
            cls.startswith("Empty") or "length" in inputs
        ):
            add(node_id, "width", "width")
            add(node_id, "height", "height")
            if "length" in inputs:
                add(node_id, "length", "frames")
            elif out.kind == "image" and "batch_size" in inputs:
                add(node_id, "batch_size", "batch")
        if "seconds" in inputs and "Latent" in cls:
            add(node_id, "seconds", "seconds")
        if cls == "CreateVideo":
            add(node_id, "fps", "fps")
        if cls == "VAEDecodeHunyuan3D":
            add(node_id, "octree_resolution", "mesh_quality")
        if cls == "LoadImageMask":
            add(node_id, "image", "mask")
        if cls in ("CheckpointLoaderSimple", "CheckpointLoader") and out.kind == "image":
            add(node_id, "ckpt_name", "model")
    pictures = [k for k in ordered if str(doc[k]["class_type"]) == "LoadImage"]
    for number, node_id in enumerate(pictures[:2]):
        add(node_id, "image", "image" if number == 0 else "image2")
    if len(pictures) > 2:
        out.notes.append(
            "It loads more than two pictures; only the first two were given a placeholder."
        )
    if not samplers and not any(c.placeholder == "seed" for c in out.changes):
        out.notes.append(
            "No sampler was recognised, so nothing was proposed for the seed and the "
            "prompt. Put the placeholders in by hand."
        )
    proposed = {change.placeholder for change in out.changes}
    for name in workflow.REQUIRED.get(out.kind, ()):
        if name not in proposed and name not in workflow.placeholders_in(doc):
            out.notes.append(
                f"A workflow of this kind needs {{{{{name}}}}} and no place for it was found."
            )
    return out


def apply(doc: dict[str, Any], changes: list[Change], accepted: set[str]) -> dict[str, Any]:
    """A copy of the document with exactly the accepted changes made."""
    out = copy.deepcopy(doc)
    for change in changes:
        if change.key in accepted and change.node in out:
            out[change.node].setdefault("inputs", {})[change.input] = (
                "{{" + change.placeholder + "}}"
            )
    return out


__all__ = ["Change", "ImportError_", "Proposal", "apply", "kind_of", "parse", "propose"]
