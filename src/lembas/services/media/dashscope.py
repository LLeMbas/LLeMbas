"""Alibaba Cloud Model Studio's native ("DashScope") API, as far as media
needs it: where it lives, how it says no, and how a result is collected.

Speech synthesis and most picture models have no OpenAI-shaped route there;
they are reached at `<host>/api/v1/services/...` on the **same host and with
the same key** as the connection's OpenAI-compatible address
(`…/compatible-mode/v1`), on the pay-as-you-go hosts and the Token Plan's
alike. A result is not returned as bytes but as a **link that expires** (a
day, the provider says): it is fetched here, at once, and what the person is
given is the bytes -- never the link.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from lembas.config import settings as env_settings
from lembas.services.llm import providers
from lembas.services.llm.openai_client import Endpoint, LLMError, wrap_transport_error
from lembas.web.i18n import t

GENERATION = "services/aigc/multimodal-generation/generation"
SPEECH = "services/audio/tts/SpeechSynthesizer"

# How much of a provider's own words reaches a person or a model.
MESSAGE_MAX = 300
# Ceilings on what a result link may hand back: a reply read aloud, a picture.
MAX_AUDIO_BYTES = 64 * 1024 * 1024
MAX_IMAGE_BYTES = 48 * 1024 * 1024


def native_url(endpoint: Endpoint, path: str) -> str:
    """`<scheme>://<host>/api/v1/<path>` for a connection whose base URL is
    the provider's OpenAI-compatible one."""
    parts = urlsplit(endpoint.base_url)
    if not parts.scheme or not parts.netloc:
        raise LLMError(t("This connection's base URL is not an address."))
    return f"{parts.scheme}://{parts.netloc}/api/v1/{path.lstrip('/')}"


def _headers(endpoint: Endpoint) -> dict[str, str]:
    return endpoint.headers()


def error_of(endpoint: Endpoint, response: httpx.Response) -> LLMError:
    """A refusal in the provider's words (its flat `code` and `message`, or
    the OpenAI-shaped `error` object of the compatible routes), cut short."""
    code = message = ""
    body: Any = None
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        nested = body.get("error") if isinstance(body.get("error"), dict) else {}
        code = str(body.get("code") or nested.get("code") or "")
        message = str(body.get("message") or nested.get("message") or "")
    if providers.quota_exhausted(
        endpoint.provider_type, endpoint.base_url, response.status_code,
        body if isinstance(body, dict) else {"code": code, "message": message},
    ) or (
        response.status_code == 429
        and "insufficient_quota" in f"{code} {message}"
        and providers.subscription(endpoint.provider_type, endpoint.base_url)
    ):
        return LLMError(t(providers.QUOTA_EXHAUSTED), status_code=429)
    said = " ".join(part for part in (code, message) if part).strip()
    said = "".join(ch for ch in said if ch.isprintable())[:MESSAGE_MAX]
    return LLMError(
        t("%(name)s answered %(status)s: %(said)s", name=endpoint.name or endpoint.base_url,
          status=response.status_code, said=said or response.reason_phrase),
        status_code=response.status_code,
    )


async def post(
    endpoint: Endpoint, url: str, body: dict[str, Any], *, timeout: float | None = None
) -> dict[str, Any]:
    """One JSON request with the connection's key; the parsed answer."""
    try:
        async with httpx.AsyncClient(timeout=timeout or env_settings.request_timeout) as client:
            response = await client.post(url, headers=_headers(endpoint), json=body)
    except httpx.RequestError as exc:
        raise wrap_transport_error(exc, endpoint) from exc
    if response.status_code >= 400:
        raise error_of(endpoint, response)
    try:
        payload = response.json()
    except ValueError as exc:
        raise LLMError(t("The endpoint returned a response that was not JSON.")) from exc
    if not isinstance(payload, dict):
        raise LLMError(t("The endpoint returned a response that was not JSON."))
    # The native API can answer 200 with a refusal inside.
    if payload.get("code") and "output" not in payload and "choices" not in payload:
        raise error_of(endpoint, httpx.Response(400, json=payload))
    return payload


async def collect(url: Any, *, limit: int, endpoint: Endpoint | None = None) -> bytes:
    """The bytes behind a result link, fetched without the connection's key.

    The link is what a provider wrote into its answer, so it is treated as an
    address somebody else chose: http or https only, never one that resolves
    to this machine or this network (`fetch.check_url`), no redirect
    followed, and no more than `limit` bytes read. One exception: a link on
    the **connection's own host** -- the address an administrator typed, as a
    local OpenAI-compatible server answers with -- is fetched wherever that
    host is."""
    from lembas.services import fetch as fetch_service

    if not isinstance(url, str) or not url.strip():
        raise LLMError(t("The provider's answer named no result."))
    try:
        own_host = bool(endpoint) and (urlsplit(url.strip()).hostname or "").lower() == (
            urlsplit(endpoint.base_url).hostname or "-"
        ).lower()
        checked = fetch_service.check_url(url.strip(), allow_private=own_host)
    except fetch_service.FetchError as exc:
        raise LLMError(t("The provider's result could not be fetched: %(why)s",
                         why=exc.message[:MESSAGE_MAX])) from exc
    data = bytearray()
    try:
        async with httpx.AsyncClient(
            timeout=env_settings.request_timeout, follow_redirects=False
        ) as client, client.stream("GET", checked) as response:
            if response.status_code != 200:
                raise LLMError(
                    t("The provider's result could not be fetched: %(why)s",
                      why=f"HTTP {response.status_code}")
                )
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > limit:
                    raise LLMError(t("The provider's result is larger than this server keeps."))
    except httpx.RequestError as exc:
        raise LLMError(
            t("The provider's result could not be fetched: %(why)s", why=type(exc).__name__)
        ) from exc
    if not data:
        raise LLMError(t("The provider's answer named no result."))
    return bytes(data)


__all__ = [
    "GENERATION",
    "MAX_AUDIO_BYTES",
    "MAX_IMAGE_BYTES",
    "SPEECH",
    "collect",
    "error_of",
    "native_url",
    "post",
]
