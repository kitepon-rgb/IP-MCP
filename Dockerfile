FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_NO_CACHE=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

# Install dependencies first (better layer caching)
COPY pyproject.toml ./
RUN uv pip install --system --no-cache .

# Copy source last
COPY src ./src

EXPOSE 8765

# Healthcheck queries the MCP server's /healthz route (added in server.py)
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8765/healthz || exit 1

CMD ["python", "-m", "ip_mcp.server"]
