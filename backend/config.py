import json
import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent
ROOT_DIR = BACKEND_DIR.parent

# .env lives at the repo root; a backend/.env (if present) overrides it.
load_dotenv(ROOT_DIR / ".env")
load_dotenv(BACKEND_DIR / ".env", override=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# LLM routing. Model IDs must be explicit; without one, an agent runs labeled simulated analysis.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
LLM_MODELS = {
    provider: {
        "fast": os.getenv(f"{provider.upper()}_FAST_MODEL", "").strip(),
        "smart": os.getenv(f"{provider.upper()}_SMART_MODEL", "").strip(),
    }
    for provider in ("gemini", "anthropic", "openai")
}
# Optional per-agent override, e.g. LLM_ROUTE_TRIAGE=anthropic:claude-haiku-5-5
LLM_ROUTES = {agent: os.getenv(f"LLM_ROUTE_{agent.upper()}", "").strip() for agent in ("triage", "research", "action")}
LLM_ROUTING = os.getenv("LLM_ROUTING", "static").strip().lower()  # static | adaptive
LLM_FALLBACK_PROVIDER = os.getenv("LLM_FALLBACK_PROVIDER", "").strip().lower()
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "90"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))
ANTHROPIC_REFUSAL_FALLBACK = os.getenv("ANTHROPIC_REFUSAL_FALLBACK", "default").strip().lower() != "off"
# USD per million tokens, {"model-id": [input, output, cache_read, cache_write]}. Costs stay
# unavailable for any model whose current price has not been verified and configured here.
LLM_PRICING: dict[str, list[float]] = json.loads(os.getenv("LLM_PRICING_JSON", "") or "{}")

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "")

DATABASE_URL = os.getenv("DATABASE_URL", "") or f"sqlite:///{BACKEND_DIR / 'relayy.db'}"
# Optional direct (non-pooled) connection used only for migrations, e.g. Neon's non "-pooler" host.
MIGRATION_DATABASE_URL = os.getenv("MIGRATION_DATABASE_URL", "")
FRONTEND_ORIGINS = [
    origin.strip().rstrip("/")
    for origin in os.getenv("FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if origin.strip()
]
# Requests per minute per client IP for state-changing endpoints (0 disables).
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))

# External effects stay disabled until an operator explicitly configures test mode.
DELIVERY_MODE = os.getenv("DELIVERY_MODE", "disabled")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM = os.getenv("RESEND_FROM", "")
RESEND_ALLOWED_RECIPIENTS = {
    value.strip().lower() for value in os.getenv("RESEND_ALLOWED_RECIPIENTS", "").split(",") if value.strip()
}
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_ACCOUNT_ID = os.getenv("STRIPE_ACCOUNT_ID", "")
STRIPE_API_VERSION = os.getenv("STRIPE_API_VERSION", "2025-02-24.acacia")
DEMO_OPERATOR_TOKEN = os.getenv("DEMO_OPERATOR_TOKEN", "")
DEMO_OPERATOR_NAME = os.getenv("DEMO_OPERATOR_NAME", "demo-operator")
SLACK_ALLOWED_TEAM_ID = os.getenv("SLACK_ALLOWED_TEAM_ID", "")
SLACK_ALLOWED_REVIEWERS = {
    value.strip() for value in os.getenv("SLACK_ALLOWED_REVIEWERS", "").split(",") if value.strip()
}
