# Development image for the PartFlow backend (Phase 1).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /uvx /usr/local/bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first so source edits reuse this cached layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen

COPY . .

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
