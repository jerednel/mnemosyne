FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependency layer first for build caching.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY site ./site
RUN uv sync --frozen --no-dev

ENV MNEMOSYNE_DATA_DIR=/data \
    MNEMOSYNE_SITE_DIR=/app/site \
    PATH="/app/.venv/bin:$PATH"

# canonical.db regenerates from bundled seed JSON on first start — the served
# tier is read-only, so the container needs no volume.
CMD ["mnemosyne-canonical"]
