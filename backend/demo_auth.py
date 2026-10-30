"""One in-memory browser credential for the trusted-local demo, not tenant auth."""

import secrets

from fastapi import HTTPException, Request

import config


def require_operator(request: Request) -> str:
    """Protect all web mutations when configured, and fail closed in delivery mode."""
    expected = config.DEMO_OPERATOR_TOKEN
    if not expected and config.DELIVERY_MODE == "disabled":
        return config.DEMO_OPERATOR_NAME
    if len(expected) < 32:
        raise HTTPException(503, "Configure a demo operator token of at least 32 characters.")
    origin = request.headers.get("origin")
    if origin and origin not in config.FRONTEND_ORIGINS:
        raise HTTPException(403, "Origin is not allowed.")
    # Non-browser JSON clients may omit Origin; browser mutations cannot be forms.
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
        raise HTTPException(415, "Use application/json.")
    provided = request.headers.get("authorization", "")
    if not secrets.compare_digest(provided.encode("utf-8"), f"Bearer {expected}".encode("utf-8")):
        raise HTTPException(401, "Unlock the demo with the operator token.")
    return config.DEMO_OPERATOR_NAME
