"""The policy a page is served under when it must reach nothing: HTML a model
wrote, or a device sent (`show`), drawn in a frame beside the conversation.

Served from its own address rather than as `srcdoc`, so it carries its own
header and inherits nothing from the page around it. `sandbox` is the half that
matters: `allow-scripts` without `allow-same-origin` makes the document an
opaque origin, so it holds no cookie of ours and cannot read or call this
application -- including when the address is opened in a tab of its own, where
no iframe attribute would apply. Nothing loads from the network (rule 2), every
resource has to be inline or a `data:`/`blob:` URL, and no form is ever sent.
"""

from __future__ import annotations


def policy() -> str:
    """The Content-Security-Policy of a sealed page."""
    return "; ".join(
        (
            "sandbox allow-scripts allow-forms",
            "default-src 'none'",
            "script-src 'unsafe-inline' 'unsafe-eval' data: blob:",
            "style-src 'unsafe-inline' data: blob:",
            "img-src data: blob:",
            "font-src data: blob:",
            "media-src data: blob:",
            "connect-src 'none'",
            "worker-src blob:",
            "frame-src 'none'",
            "object-src 'none'",
            "form-action 'none'",
            "base-uri 'none'",
            # Only this application may frame it. Not the boundary -- the page
            # is sealed whoever frames it -- but a page out of somebody's
            # conversation has no business inside another site.
            "frame-ancestors 'self'",
        )
    )


def headers() -> dict[str, str]:
    return {
        "Content-Security-Policy": policy(),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
    }


__all__ = ["headers", "policy"]
