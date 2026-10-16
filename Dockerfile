# syntax=docker/dockerfile:1

# Targets: `dev` for the compose stack, `prod` for what ships.
#
#   docker build --target prod -t circuit-bot .
#
# Both start from the same dependency layer, so a production image and the
# container a developer is running hold identical third-party code.

FROM python:3.14-slim AS base

# Pinned to the uv that wrote uv.lock, so a build resolves what a developer's
# machine resolves.  Bump the two together.
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Third-party dependencies alone, in a layer that only moves when uv.lock does.
# The project itself is deliberately left out: --no-install-project keeps the
# build backend away from src/, which is not copied in yet.
FROM base AS deps

COPY pyproject.toml uv.lock README.md ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# The project as a real installed package, for the image that ships.  The venv
# is then self-contained, and the runtime stage below needs no source tree and
# no build backend.
FROM deps AS build

COPY src ./src

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.14-slim AS prod

ENV PYTHONUNBUFFERED=1 \
    STAGE=prod \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=build /app/.venv /app/.venv

# The bot needs neither root nor a writable path: it reaches Postgres and Redis
# over the network and logs to stderr.
RUN useradd --system --create-home --uid 10001 circuit

USER circuit

ENTRYPOINT ["circuit-bot"]

# Development: the dev group as well, with the project installed editable - the
# venv points back at /app/src, which is the path compose bind-mounts the
# working tree over.  Editing a file and restarting the service is enough.
FROM deps AS dev

ENV PYTHONUNBUFFERED=1 \
    STAGE=dev

COPY src ./src

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

ENTRYPOINT ["circuit-bot"]
