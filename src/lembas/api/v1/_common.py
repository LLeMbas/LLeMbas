"""What the `/v1` modules share."""

from __future__ import annotations

from fastapi.responses import JSONResponse


def error_response(
    status_code: int, message: str, kind: str = "invalid_request_error"
) -> JSONResponse:
    """OpenAI's error shape, which every client already knows how to print."""
    return JSONResponse(
        {"error": {"message": message, "type": kind, "code": status_code}},
        status_code=status_code,
    )
