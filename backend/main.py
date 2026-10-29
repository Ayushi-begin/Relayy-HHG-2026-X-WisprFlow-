import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import config
import ticket_service
from db import create_db_and_tables
from integrations import slack
from fulfillment import service as fulfillment
from routers import slack as slack_routes
from routers import tickets as ticket_routes

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ticket_routes.router)
app.include_router(slack_routes.router)


@app.get("/api/hello")
def hello() -> dict[str, str]:
    return {"message": "Hello, world!"}


@app.get("/api/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "anthropic_configured": bool(config.ANTHROPIC_API_KEY),
        "slack_configured": slack.is_configured(),
        "slack_interactive": bool(config.SLACK_SIGNING_SECRET),
        **fulfillment.readiness(),
    }
