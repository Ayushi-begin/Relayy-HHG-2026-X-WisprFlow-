import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

import agent_service
import config
import http_security
import ticket_service
from db import create_db_and_tables, ping
from integrations import slack
from fulfillment import service as fulfillment
from routers import agents as agent_routes
from routers import slack as slack_routes
from routers import tickets as ticket_routes
from swarm import llm
from swarm.providers import health as provider_health

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    agent_service.load_controls()
    ticket_service.recover_interrupted()
    fulfillment.recover_operations()
    stop = asyncio.Event()
    task = asyncio.create_task(fulfillment.worker(stop))
    try:
        yield
    finally:
        stop.set()
        await task


app = FastAPI(title="Relayy API", lifespan=lifespan)

http_security.install(app)
# Added last so it is outermost: even rate-limit and error responses carry CORS headers.
# The operator token travels in the Authorization header, never cookies, so no credentials mode.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.FRONTEND_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
)

app.include_router(ticket_routes.router)
app.include_router(slack_routes.router)
app.include_router(agent_routes.router)


@app.get("/api/hello")
def hello() -> dict[str, str]:
    return {"message": "Hello, world!"}


@app.get("/api/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "llm_provider": config.LLM_PROVIDER,
        "llm_configured": sorted(llm.configured_providers()),
        "slack_configured": slack.is_configured(),
        "slack_interactive": bool(config.SLACK_SIGNING_SECRET),
        **fulfillment.readiness(),
    }


@app.get("/api/ready")
def ready() -> JSONResponse:
    """Readiness for the platform health check: the database must answer."""
    if not ping():
        return JSONResponse({"status": "unavailable", "database": False}, status_code=503)
    return JSONResponse({"status": "ok", "database": True})


@app.get("/api/providers")
def providers() -> dict[str, object]:
    """Routing configuration and in-process health. Never includes credentials."""
    configured = llm.configured_providers()
    routes = {agent: llm.route(agent) for agent in ("triage", "research", "action")}
    return {
        "default_provider": config.LLM_PROVIDER,
        "routing": config.LLM_ROUTING,
        "fallback_provider": config.LLM_FALLBACK_PROVIDER or None,
        "providers": {
            name: {"configured": name in configured, "models": config.LLM_MODELS.get(name, {})}
            for name in llm.providers()
        },
        "routes": {
            agent: (route.__dict__ if route else None) for agent, route in routes.items()
        },
        "health": provider_health.snapshot(),
    }
