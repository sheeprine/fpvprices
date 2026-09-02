FROM python:3.13-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.11.15 /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first (cached layer — only invalidated when lock file changes)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application source
COPY main.py database.py scraper.py scheduler.py alembic.ini ./
COPY plugins/ plugins/
COPY templates/ templates/
COPY migrations/ migrations/

# Persistent storage for the SQLite database
RUN mkdir -p /data && \
    useradd -r -u 1001 -s /bin/false -d /nonexistent appuser && \
    chown -R appuser /app /data

USER appuser

ENV DATABASE_URL=sqlite:////data/fpvprices.db
ENV UV_NO_CACHE=1

EXPOSE 8000

VOLUME ["/data"]

CMD ["uv", "run", "--no-sync", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
