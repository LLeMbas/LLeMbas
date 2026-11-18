"""Media from connections: speech to text, text to speech and pictures.

The instance has **defaults** -- the two audio endpoints (Admin › Audio) and
the ComfyUI (Admin › Image generation). A connection may also carry media
models of its own (`Connection.media_json`, `store.py`), and two switches say
whether a chat whose model is on that connection uses them.

The parts:

- `store.py` -- what a connection offers: its models per capability, each
  enabled and priced on its own, the two switches, and what a known provider
  type is pre-filled with.
- `sources.py` -- the one decision: for a capability and a chat's connection,
  the **sources to try, in order** -- the connection's own (switch on, a model
  enabled, and allowed for this kind of work), then the instance's default.
  Never the other way: a chat on a local model is sent to a cloud
  connection's media only when the administrator made that the instance's
  default themselves.
- `speech.py` and `pictures.py` -- one function per capability
  (`transcribe`, `speak`, `generate`) over interchangeable sources: an
  OpenAI-shaped endpoint, Alibaba Cloud Model Studio's native API
  (`dashscope.py`), and for pictures the instance's ComfyUI (which stays in
  `services/images/`).

Media on an interactive-only connection (a provider's plan,
`services/interactive.py`) follows the rule chat does: a person's own turn
only. What is not one -- a scheduled run's image tool, a personal API key on
`/v1` -- skips such a source.
"""

from lembas.services.media.store import (
    CAP_IMAGE,
    CAP_STT,
    CAP_TTS,
    CAPABILITIES,
)

__all__ = ["CAPABILITIES", "CAP_IMAGE", "CAP_STT", "CAP_TTS"]
