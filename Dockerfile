# syntax=docker/dockerfile:1

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app/ app/
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
ARG BOT_VERSION=dev
ENV BOT_VERSION=$BOT_VERSION
RUN useradd --create-home --shell /bin/bash app
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/app /app/app
ENV PATH="/app/.venv/bin:$PATH"
USER app
CMD ["python", "-m", "app"]
