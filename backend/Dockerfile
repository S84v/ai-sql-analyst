# syntax=docker/dockerfile:1

# Cloud Run runs one stateless FastAPI process; a single slim Python image is
# sufficient. Use a slim Python runtime and pin uv for predictable builds.
FROM python:3.12-slim

# uv ships as a static binary; copy it from the official image rather than
# installing it with pip.
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /uvx /bin/

# Compile bytecode now (faster cold starts), copy instead of hardlink across
# filesystems, and keep stdout unbuffered so Cloud Run captures logs promptly.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Locked runtime dependencies only (no dev, no evals). Copying metadata alone
# keeps this layer cached when application source changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

# Install the application non-editable from the package source.
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

ENV PATH="/app/.venv/bin:$PATH"

# Cloud Run injects $PORT and expects the server to listen on all interfaces.
# 8080 is the documented Cloud Run default and only a local fallback.
CMD ["sh", "-c", "uvicorn ai_sql_analyst.api:app --host 0.0.0.0 --port ${PORT:-8080}"]
