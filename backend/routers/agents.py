"""Agent management and audit API."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

import agent_service
import audit
from demo_auth import require_operator

router = APIRouter()


class AgentSettingsUpdate(BaseModel):
    model_enabled: bool


@router.get("/agents")
def list_agents() -> list[dict[str, Any]]:
    return list(agent_service.describe().values())


@router.post("/agents/{agent}/settings")
def update_agent(agent: str, payload: AgentSettingsUpdate, actor: str = Depends(require_operator)) -> dict[str, Any]:
    try:
        return agent_service.set_model_enabled(agent, payload.model_enabled, actor)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown agent {agent!r}")


@router.get("/audit")
def list_audit(limit: int = Query(default=100, ge=1, le=500), _: str = Depends(require_operator)) -> list[dict[str, Any]]:
    return audit.recent(limit)
