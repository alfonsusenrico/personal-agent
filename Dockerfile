# syntax=docker/dockerfile:1
FROM python:3.13-slim-trixie AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /bin/uv
# uv fetches the gateway client from its pinned git commit.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.13-slim-trixie AS runtime
RUN useradd --system --uid 10001 --no-create-home agent
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
ARG APP_REVISION=unknown
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_REVISION=${APP_REVISION}
USER agent
CMD ["personal-agent-worker"]

# The backup service, the restore check, and the review: restic and a PostgreSQL 16
# client. Debian's own client is 17, whose dumps a 16 server cannot restore cleanly,
# so it comes from the PostgreSQL apt repository, set up by the script and key that
# Debian's postgresql-common package ships.
FROM runtime AS backup
USER root
RUN apt-get update \
    && apt-get install --yes --no-install-recommends postgresql-common ca-certificates \
    && /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y \
    && apt-get install --yes --no-install-recommends postgresql-client-16 restic \
    && rm -rf /var/lib/apt/lists/*
USER agent
CMD ["personal-agent-backup"]
