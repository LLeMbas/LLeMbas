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


def page_policy() -> str:
    """The policy of **a page a model wrote into an answer** (harness spec
    3.8.0, `pages.md` §6). Tighter than `policy()`, which serves a file the
    person chose to open: what is on such a page may have been steered by
    anything the model read, so beyond reaching nothing of this application
    it must not be able to *send* anything anywhere -- a page that could
    would be a way to carry a conversation out of the instance.

    **No script runs.** With scripts, two ways out stay open that no policy
    of a web page closes (`tests/test_html_pages_sealed.py` measures both): a
    peer connection to a STUN server of the page's choosing, and a name
    looked up for a `<link rel="dns-prefetch">` a script may add at any time.
    So the page is HTML, CSS and inline SVG, rebuilt by
    `services/html_pages.document` without anything that names another host.

    Navigating the frame is not something a document's own policy can
    refuse; that is the framing page's `frame-src 'self'`
    (`security/hardening.py`), and the route refuses to be opened as a
    document of its own (`api/html_pages.py`).
    """
    return "; ".join(
        (
            "sandbox",
            "default-src 'none'",
            "script-src 'none'",
            "style-src 'unsafe-inline'",
            "img-src data: blob:",
            "font-src data:",
            "media-src 'none'",
            "connect-src 'none'",
            "worker-src 'none'",
            "child-src 'none'",
            "frame-src 'none'",
            "object-src 'none'",
            "manifest-src 'none'",
            "form-action 'none'",
            "base-uri 'none'",
            "webrtc 'block'",
            "frame-ancestors 'self'",
        )
    )


def page_headers() -> dict[str, str]:
    return {
        **headers(),
        "Content-Security-Policy": page_policy(),
        # No DNS lookups for names in the page before anything is clicked.
        "X-DNS-Prefetch-Control": "off",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    }


def headers() -> dict[str, str]:
    return {
        "Content-Security-Policy": policy(),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
    }


__all__ = ["headers", "page_headers", "page_policy", "policy"]
