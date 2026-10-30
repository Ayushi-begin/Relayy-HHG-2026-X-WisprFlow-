from fastapi import FastAPI
from fastapi.testclient import TestClient

import http_security
import main


def _app(limit: int) -> TestClient:
    app = FastAPI()
    app.add_middleware(http_security.SecurityMiddleware, limiter=http_security.RateLimiter(limit))

    @app.post("/tickets")
    def create() -> dict:
        return {"ok": True}

    @app.post("/slack/interactions")
    def slack() -> dict:
        return {"ok": True}

    @app.get("/tickets")
    def read() -> dict:
        return {"ok": True}

    return TestClient(app)


def test_mutations_are_rate_limited_per_client_with_retry_after():
    client = _app(limit=2)
    assert client.post("/tickets").status_code == 200
    assert client.post("/tickets").status_code == 200
    limited = client.post("/tickets")
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1
    # Reads and signed Slack deliveries are not throttled by this limiter.
    assert client.get("/tickets").status_code == 200
    assert client.post("/slack/interactions").status_code == 200


def test_security_headers_are_added():
    response = _app(limit=10).get("/tickets")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_limiter_window_expires():
    limiter = http_security.RateLimiter(1, window=0.0)
    assert limiter.allow("a")[0] and limiter.allow("a")[0]


def test_unhandled_errors_do_not_leak_details():
    app = FastAPI()
    http_security.install(app)

    @app.get("/boom")
    def boom():
        raise RuntimeError("password=hunter2 SELECT * FROM secrets")

    response = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error."}
    assert "hunter2" not in response.text


def test_readiness_reports_database_state(monkeypatch):
    client = TestClient(main.app)  # no lifespan: nothing is migrated or started
    monkeypatch.setattr(main, "ping", lambda: False)
    assert client.get("/api/ready").status_code == 503
    monkeypatch.setattr(main, "ping", lambda: True)
    assert client.get("/api/ready").json() == {"status": "ok", "database": True}


def test_cors_allows_only_configured_origins():
    client = TestClient(main.app)
    allowed = client.options("/tickets", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type",
    })
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    denied = client.options("/tickets", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in denied.headers
