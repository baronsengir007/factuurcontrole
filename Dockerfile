# Python 3.12 + uv for the service, Node 22 for the Claude CLI (the live model client, ADR 0003).
# Every version is pinned. No secret is baked in: .env stays out of the image (.dockerignore).
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    DATABASE_PATH=/data/app.db
COPY --from=ghcr.io/astral-sh/uv:0.8.14 /uv /uvx /bin/
COPY --from=node:22-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && npm i -g @anthropic-ai/claude-code@2.1.312

WORKDIR /app
COPY pyproject.toml uv.lock ./
# The dev group (pytest, httpx) comes along on purpose: a reviewer can run the tests and the
# eval inside the container, without installing anything.
RUN uv sync --frozen
COPY app/ app/
COPY config/ config/
COPY data/seed/ data/seed/
COPY data/cassettes/ data/cassettes/
COPY evals/ evals/
COPY tests/ tests/

EXPOSE 8000
# init: schema, the synthetic seed when the database is empty, and contract intake with
# whatever model client is available. Idempotent.
CMD ["sh", "-c", "python -m app.cli init && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
