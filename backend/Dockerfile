# Production image for the Relayy API. One process: the SSE bus, rate limiter and delivery
# worker are in-process, so run a single instance (see DEPLOYMENT.md before scaling out).
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
RUN useradd --create-home --uid 10001 relayy

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=relayy:relayy . .
USER relayy

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:' + __import__('os').getenv('PORT', '8000') + '/api/ready', timeout=4)"

# --proxy-headers makes rate limiting see the real client IP behind the platform proxy.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
