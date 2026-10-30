"""HTTP hardening: security headers, a per-IP rate limit for mutations, and safe error bodies.

The rate limiter is in-process (one Render instance). It bounds abuse of model-spending and
state-changing endpoints; it is not a substitute for authentication.
"""

import logging
import threading
import time
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

import config

log = logging.getLogger(__name__)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-site",
    # The API serves JSON and event streams only; nothing it returns should run as a page.
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
# Slack retries signed deliveries; its signature check is the gate there.
RATE_LIMIT_EXEMPT_PREFIXES = ("/slack/",)
WINDOW_SECONDS = 60.0


class RateLimiter:
    def __init__(self, limit: int, window: float = WINDOW_SECONDS) -> None:
        self.limit, self.window = limit, window
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds) for one request from `key`."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False, max(1, int(hits[0] + self.window - now) + 1)
            hits.append(now)
            if len(self._hits) > 10_000:  # bound memory under many distinct clients
                for stale in [k for k, v in self._hits.items() if not v or v[-1] <= now - self.window]:
                    del self._hits[stale]
            return True, 0


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limiter: RateLimiter | None) -> None:
        super().__init__(app)
        self.limiter = limiter

    async def dispatch(self, request: Request, call_next):
        if (
            self.limiter is not None
            and request.method in MUTATING
            and not request.url.path.startswith(RATE_LIMIT_EXEMPT_PREFIXES)
        ):
            client = request.client.host if request.client else "unknown"
            allowed, retry_after = self.limiter.allow(client)
            if not allowed:
                return JSONResponse(
                    {"detail": "Too many requests. Slow down and retry shortly."},
                    status_code=429, headers={"Retry-After": str(retry_after), **SECURITY_HEADERS},
                )
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response


def install(app: FastAPI) -> None:
    limit = config.RATE_LIMIT_PER_MINUTE
    app.add_middleware(SecurityMiddleware, limiter=RateLimiter(limit) if limit > 0 else None)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Never echo internal exception text (it can contain provider bodies or SQL).
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "Internal server error."}, status_code=500, headers=SECURITY_HEADERS)
