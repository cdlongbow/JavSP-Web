FROM python:3.12-slim AS builder

WORKDIR /app

ENV PATH=/root/.local/bin:$PATH \
    POETRY_HTTP_TIMEOUT=300

RUN pip install pipx && \
    pipx ensurepath && \
    pipx install poetry==1.8.3
RUN apt update && \
    apt install -y --no-install-recommends git build-essential cmake libgomp1 && \
    rm -rf /var/lib/apt/lists/*

# Install dev dependencies for cx-freeze compilation (e.g. lxml编译需要libxml2)
# poetry-dynamic-versioning is required at BUILD TIME only; inject it into poetry
RUN pipx inject poetry poetry-dynamic-versioning

COPY pyproject.toml pyproject.lock* poetry.lock* ./

RUN poetry config virtualenvs.in-project true && \
    poetry lock --no-update && \
    poetry install --no-interaction --no-ansi --only main --no-root

FROM python:3.12-slim AS runner

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["server"]
