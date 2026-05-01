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

# Bring in metadata + lockfile + readme + source, then install in one step
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv pip install --system --no-cache .

EXPOSE 8765

# Healthcheck: TCP listen probe via stdlib socket (no curl/healthz route needed)
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8765), timeout=2).close()" || exit 1

CMD ["python", "-m", "ip_mcp.server"]
