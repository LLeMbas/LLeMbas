"""What is behind a URL, and what its models can do.

**Detection from a pasted URL** (`detect`), for the first-run wizard and the
add-connection form. A known cloud host is its type without a request being
made. Anything else is asked, **at the pasted address only** -- never another
host, and redirects are not followed -- what it is, in this order:

| asked | answer that names it | server |
|---|---|---|
| `GET /running` | `{"running": [...]}` | llama-swap |
| `GET /props` | `default_generation_settings` or `chat_template` | llama.cpp |
| `GET /api/version` | `{"version": ...}` | Ollama |
| `GET /api/v0/models` | `{"data": [... "max_context_length" ...]}` | LM Studio |
| `GET /v1/models` | entries with `max_model_len` / `owned_by: vllm` | vLLM |
| `GET /v1/models` | any OpenAI-shaped list | an OpenAI-compatible server |

All of them are `openai_compatible`; the server is noted, and what follows
from it is suggested (llama-swap holds one model at a time and unloads with
`/unload`). The key, when one is given, goes only with the `/v1/models` call,
to the address it was typed for.

**Capabilities** (`model_capabilities`), on a refresh: llama.cpp's `/props`
(context, vision, tools from the chat template), Ollama's `POST /api/show`
(context from `model_info`, and its `capabilities` list), LM Studio's
`/api/v0/models`, OpenRouter's `/models` (context, input modalities, supported
parameters), and whatever `/v1/models` itself carries (vLLM's `max_model_len`,
Anthropic's limits). A server that says nothing leaves nothing detected --
never a guess.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlsplit

import httpx

from lembas.services.llm import providers
from lembas.services.llm.openai_client import Endpoint, context_from
from lembas.web.i18n import N_

log = logging.getLogger(__name__)

PROBE_TIMEOUT = 5.0

SERVER_LLAMA_CPP = "llama.cpp"
SERVER_LLAMA_SWAP = "llama-swap"
SERVER_OLLAMA = "ollama"
SERVER_VLLM = "vllm"
SERVER_LMSTUDIO = "lmstudio"
SERVER_OPENAI_COMPATIBLE = "openai-compatible"


def _root(url: str) -> str:
    """The server's root: the URL less a trailing `/v1` (and slash)."""
    base = url.strip().rstrip("/")
    return base[: -len("/v1")] if base.endswith("/v1") else base


async def _get(client: httpx.AsyncClient, url: str, headers: dict | None = None) -> Any:
    """GET `url` as JSON, or None for anything that is not a 200 with JSON."""
    try:
        response = await client.get(url, headers=headers or {})
    except (httpx.HTTPError, ValueError):
        return None
    if response.status_code != 200:
        return None
    try:
        return response.json()
    except (ValueError, json.JSONDecodeError):
        return None


def _client() -> httpx.AsyncClient:
    # No redirects: a probe never goes anywhere but the address it was given.
    return httpx.AsyncClient(timeout=PROBE_TIMEOUT, follow_redirects=False)


async def detect(url: str, api_key: str = "") -> dict[str, Any]:
    """What the pasted URL is.

    `{"ok", "provider_type", "base_url", "server", "models", "error",
    "suggest": {"one_model_at_a_time", "unload_url"}}`. Never raises.
    """
    url = (url or "").strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        parts = None
    if parts is None or parts.scheme not in ("http", "https") or not parts.hostname:
        return {"ok": False, "error": N_("The base URL must start with http:// or https://")}
    known = providers.by_host(url)
    if known is not None:
        return {
            "ok": True,
            "provider_type": known.id,
            "base_url": known.default_base_url,
            "server": known.id,
            "models": None,
            "suggest": {},
        }
    root = _root(url)
    out: dict[str, Any] = {
        "ok": True,
        "provider_type": providers.OPENAI_COMPATIBLE,
        "base_url": f"{root}/v1",
        "server": "",
        "models": None,
        "suggest": {},
    }
    async with _client() as client:
        running = await _get(client, f"{root}/running")
        if isinstance(running, dict) and isinstance(running.get("running"), list):
            out["server"] = SERVER_LLAMA_SWAP
            out["suggest"] = {"one_model_at_a_time": True, "unload_url": f"{root}/unload"}
        else:
            props = await _get(client, f"{root}/props")
            if isinstance(props, dict) and (
                "default_generation_settings" in props or "chat_template" in props
            ):
                out["server"] = SERVER_LLAMA_CPP
            else:
                version = await _get(client, f"{root}/api/version")
                if isinstance(version, dict) and isinstance(version.get("version"), str):
                    out["server"] = SERVER_OLLAMA
                else:
                    studio = await _get(client, f"{root}/api/v0/models")
                    if isinstance(studio, dict) and any(
                        isinstance(m, dict) and "max_context_length" in m
                        for m in studio.get("data") or []
                    ):
                        out["server"] = SERVER_LMSTUDIO
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        listed = await _get(client, f"{root}/v1/models", headers)
    entries = listed.get("data") if isinstance(listed, dict) else listed
    if isinstance(entries, list):
        out["models"] = len(entries)
        if not out["server"]:
            vllm = any(
                isinstance(e, dict) and ("max_model_len" in e or e.get("owned_by") == "vllm")
                for e in entries
            )
            out["server"] = SERVER_VLLM if vllm else SERVER_OPENAI_COMPATIBLE
    elif not out["server"]:
        return {
            "ok": False,
            "error": N_("Nothing at that address answered like a model server."),
            "base_url": out["base_url"],
        }
    return out


# --- Capabilities ------------------------------------------------------------------------
def _from_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """What a `/v1/models` entry itself says (vLLM, Anthropic, OpenRouter)."""
    found: dict[str, Any] = {}
    length = context_from(entry)
    if length:
        found["context_length"] = length
    architecture = entry.get("architecture")
    if isinstance(architecture, dict):
        modalities = architecture.get("input_modalities")
        if isinstance(modalities, list):
            found["vision"] = "image" in modalities
    supported = entry.get("supported_parameters")
    if isinstance(supported, list):
        found["tools"] = "tools" in supported
        found["reasoning"] = "reasoning" in supported or "include_reasoning" in supported
    return found


def _from_props(props: dict[str, Any]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    settings = props.get("default_generation_settings")
    n_ctx = settings.get("n_ctx") if isinstance(settings, dict) else None
    if isinstance(n_ctx, int) and not isinstance(n_ctx, bool) and n_ctx > 0:
        found["context_length"] = n_ctx
    modalities = props.get("modalities")
    if isinstance(modalities, dict) and isinstance(modalities.get("vision"), bool):
        found["vision"] = modalities["vision"]
    template = props.get("chat_template")
    if isinstance(template, str) and template:
        found["tools"] = "tools" in template
    return found


def _from_ollama(show: dict[str, Any]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    info = show.get("model_info")
    if isinstance(info, dict):
        for key, value in info.items():
            if key.endswith(".context_length") and isinstance(value, int):
                found["context_length"] = value
                break
    capabilities = show.get("capabilities")
    if isinstance(capabilities, list):
        found["vision"] = "vision" in capabilities
        found["tools"] = "tools" in capabilities
        found["reasoning"] = "thinking" in capabilities
        found["embeddings"] = "embedding" in capabilities
    return found


def _from_lmstudio(entry: dict[str, Any]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    length = entry.get("max_context_length")
    if isinstance(length, int) and not isinstance(length, bool) and length > 0:
        found["context_length"] = length
    kind = entry.get("type")
    if isinstance(kind, str):
        found["vision"] = kind == "vlm"
        found["embeddings"] = kind == "embeddings"
    return found


async def model_capabilities(
    endpoint: Endpoint, entries: list[dict[str, Any]], server: str = ""
) -> dict[str, tuple[dict[str, Any], str]]:
    """Per model id, what the provider says it can do and which call said it.
    Never raises; a model nothing was learned about is absent."""
    out: dict[str, tuple[dict[str, Any], str]] = {}
    for entry in entries:
        found = _from_entry(entry)
        if found:
            out[str(entry.get("id"))] = (found, "/models")
    if endpoint.is_anthropic or providers.get(endpoint.provider_type).id != (
        providers.OPENAI_COMPATIBLE
    ):
        return out
    root = _root(endpoint.base_url)
    headers = {k: v for k, v in endpoint.headers().items() if k != "Content-Type"}
    try:
        async with _client() as client:
            if server in ("", SERVER_LLAMA_CPP):
                props = await _get(client, f"{root}/props", headers)
                if isinstance(props, dict) and "default_generation_settings" in props:
                    found = _from_props(props)
                    # One model behind a llama-server: every listed id is it.
                    for entry in entries:
                        if found:
                            out[str(entry.get("id"))] = (found, "/props")
                    return out
            if server in ("", SERVER_OLLAMA):
                version = await _get(client, f"{root}/api/version", headers)
                if isinstance(version, dict) and "version" in version:
                    for entry in entries:
                        name = str(entry.get("id"))
                        try:
                            response = await client.post(
                                f"{root}/api/show", json={"model": name}, headers=headers
                            )
                            show = response.json() if response.status_code == 200 else None
                        except (httpx.HTTPError, ValueError):
                            show = None
                        if isinstance(show, dict):
                            found = _from_ollama(show)
                            if found:
                                out[name] = (found, "/api/show")
                    return out
            if server in ("", SERVER_LMSTUDIO):
                studio = await _get(client, f"{root}/api/v0/models", headers)
                if isinstance(studio, dict):
                    for entry in studio.get("data") or []:
                        if isinstance(entry, dict) and entry.get("id"):
                            found = _from_lmstudio(entry)
                            if found:
                                out[str(entry["id"])] = (found, "/api/v0/models")
    except Exception:  # detection is a nicety; a refresh must not fail on it
        log.warning("capability detection failed for %s", endpoint.base_url, exc_info=True)
    return out


__all__ = [
    "SERVER_LLAMA_CPP",
    "SERVER_LLAMA_SWAP",
    "SERVER_LMSTUDIO",
    "SERVER_OLLAMA",
    "SERVER_OPENAI_COMPATIBLE",
    "SERVER_VLLM",
    "detect",
    "model_capabilities",
]
