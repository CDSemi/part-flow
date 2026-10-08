# PartFlow backend images: `production` (Phase 16) and `development` (default, last stage).
FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /uvx /usr/local/bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Runtime dependencies only (no dev group), resolved from the lock file.
FROM base AS production-deps
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Production: the locked virtual environment and the application only — no
# tests, no .env, no uv, no reload; runs as an unprivileged user. Workers
# (WEB_CONCURRENCY) and trusted proxies (FORWARDED_ALLOW_IPS) come from
# compose.production.yaml. Migrations run only as the explicit `migrate` job.
# Same base tag as `base`, so the copied venv's interpreter path stays valid.
FROM python:3.12-slim AS production
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:${PATH}"
RUN groupadd --system --gid 10001 partflow \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin partflow
WORKDIR /app
COPY --from=production-deps /app/.venv /app/.venv
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

# Development image (Phase 1): the default target of compose.yaml (last stage).
FROM base AS development

# Install dependencies first so source edits reuse this cached layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen

COPY . .

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
