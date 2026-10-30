import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent
ROOT_DIR = BACKEND_DIR.parent

# .env lives at the repo root; a backend/.env (if present) overrides it.
load_dotenv(ROOT_DIR / ".env")
load_dotenv(BACKEND_DIR / ".env", override=True)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "")

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BACKEND_DIR / 'relayy.db'}")
FRONTEND_ORIGINS = os.getenv(
    "FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",")

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
