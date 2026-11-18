"""Starter workflows: one way to make each kind of thing, to begin from.

Tracked JSON beside `base_workflow.json`, in ComfyUI's API format, built on
the nodes ComfyUI itself ships (`comfy_extras/`) and on the models its own
example workflows use. **None of them was run by the people who wrote this
file**: each names model files an administrator has to have put into their
ComfyUI's folders, and what a given ComfyUI version calls a node's inputs
moves. So a starter is added like any workflow, switched off, and its page
says which files it expects and that it is unverified until *Test* passes
there.

They are conservative on purpose: the sampling values a model family wants
(steps, cfg, sampler, shift) are literals from that family's reference
workflow, not placeholders -- the instance's picture defaults (cfg 8, 512 px)
would be wrong for every one of them. What a request varies is what is left:
the prompt, the input picture, the seed, the length, the size.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lembas.services.images import workflow

DIRECTORY = Path(__file__).parent / "starters"


@dataclass(frozen=True)
class Starter:
    key: str
    kind: str
    name: str
    description: str
    # Where the model files go in a ComfyUI's `models/` folder, by file.
    folders: tuple[tuple[str, str], ...]

    def template(self) -> dict[str, Any]:
        return json.loads((DIRECTORY / f"{self.key}.json").read_text(encoding="utf-8"))

    @property
    def files(self) -> list[str]:
        return workflow.model_files(self.template())


STARTERS: tuple[Starter, ...] = (
    Starter(
        "image_edit", "image_edit", "Picture edit (Qwen-Image-Edit)",
        "Changes one picture as described: add, remove, restyle.",
        (("qwen_image_edit_fp8_e4m3fn.safetensors", "diffusion_models"),
         ("qwen_2.5_vl_7b_fp8_scaled.safetensors", "text_encoders"),
         ("qwen_image_vae.safetensors", "vae")),
    ),
    Starter(
        "video_t2v", "video", "Text to video (Wan 2.1, 1.3B)",
        "A short clip from a description. The small model: about 8 GB of video memory.",
        (("wan2.1_t2v_1.3B_fp16.safetensors", "diffusion_models"),
         ("umt5_xxl_fp8_e4m3fn_scaled.safetensors", "text_encoders"),
         ("wan_2.1_vae.safetensors", "vae")),
    ),
    Starter(
        "video_i2v", "video", "Picture to video (Wan 2.1, 480p)",
        "A short clip that starts from a picture. Needs a first picture.",
        (("wan2.1_i2v_480p_14B_fp8_scaled.safetensors", "diffusion_models"),
         ("umt5_xxl_fp8_e4m3fn_scaled.safetensors", "text_encoders"),
         ("wan_2.1_vae.safetensors", "vae"),
         ("clip_vision_h.safetensors", "clip_vision")),
    ),
    Starter(
        "music", "music", "Text to music (ACE-Step)",
        "A track from tags (genre, instruments, mood) and, if given, lyrics.",
        (("ace_step_v1_3.5b.safetensors", "checkpoints"),),
    ),
    Starter(
        "model3d", "model3d", "Picture to 3D model (Hunyuan3D 2.0)",
        "A 3D model (GLB) of the one object in a picture.",
        (("hunyuan3d-dit-v2.safetensors", "checkpoints"),),
    ),
)


def get(key: str) -> Starter | None:
    return next((starter for starter in STARTERS if starter.key == key), None)


def of_kind(kind: str) -> list[Starter]:
    return [starter for starter in STARTERS if starter.kind == kind]


__all__ = ["STARTERS", "Starter", "get", "of_kind"]
