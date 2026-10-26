# syntax=docker/dockerfile:1

# Targets: `dev` for the compose stack, `prod` for what ships.
#
#   docker build --target prod -t circuit-bot .

FROM python:3.14-slim AS base

# Pinned to the uv that wrote uv.lock - bump the two together.
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies only, so this layer moves when uv.lock does and not when src does.
FROM base AS deps

COPY pyproject.toml uv.lock README.md ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# The project as an installed package, so the runtime stage needs no source tree.
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

# League settings; deployments can override with CONFIG_PATH to a mounted file.
COPY config.toml ./

# Needs neither root nor a writable path.
RUN useradd --system --create-home --uid 10001 circuit

USER circuit

ENTRYPOINT ["circuit-bot"]

# Dev: the dev group too, and the project editable so the bind-mounted source
# in compose is the code that runs.
FROM deps AS dev

ENV PYTHONUNBUFFERED=1 \
    STAGE=dev

COPY src ./src

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

ENTRYPOINT ["circuit-bot"]
