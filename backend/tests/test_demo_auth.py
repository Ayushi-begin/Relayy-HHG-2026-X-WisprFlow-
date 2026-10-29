import httpx
import pytest
from fastapi import Depends, FastAPI

import config
from demo_auth import require_operator


@pytest.mark.asyncio
async def test_test_mode_requires_token_json_and_allowed_origin(monkeypatch):
    monkeypatch.setattr(config, "DELIVERY_MODE", "test")
    monkeypatch.setattr(config, "DEMO_OPERATOR_TOKEN", "x" * 40)
    app = FastAPI()

    @app.post("/mutate")
    def mutate(actor: str = Depends(require_operator)):
        return {"actor": actor}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/mutate", json={})).status_code == 401
        headers = {"authorization": "Bearer " + "x" * 40}
        assert (await client.post("/mutate", headers=headers)).status_code == 415
        assert (await client.post("/mutate", json={}, headers={**headers, "origin": "https://evil.example"})).status_code == 403
        assert (await client.post("/mutate", json={}, headers=headers)).status_code == 200
        monkeypatch.setattr(config, "DEMO_OPERATOR_TOKEN", "")
        assert (await client.post("/mutate", json={}, headers=headers)).status_code == 503


@pytest.mark.asyncio
async def test_disabled_offline_demo_does_not_require_missing_token(monkeypatch):
    monkeypatch.setattr(config, "DELIVERY_MODE", "disabled")
    monkeypatch.setattr(config, "DEMO_OPERATOR_TOKEN", "")
    app = FastAPI()

    @app.post("/mutate")
    def mutate(actor: str = Depends(require_operator)):
        return {"actor": actor}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/mutate", json={})).status_code == 200
