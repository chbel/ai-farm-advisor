FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# Test stage: docker build --target test services/ai-advisor-service
# (database tests are skipped here; run them on the compose network, see README)
FROM base AS test
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
COPY . .
RUN python -m pytest -q -p no:cacheprovider

# Runtime image (default target).
FROM base AS runtime
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
RUN useradd --create-home --uid 1000 appuser && mkdir -p /app/data && chown appuser /app/data
USER appuser

# Embedding model files and downloaded documents (mounted as a volume).
ENV DATA_DIR=/app/data
EXPOSE 8006

# First start downloads the embedding model (~470 MB): allow a long start period.
HEALTHCHECK --interval=15s --timeout=5s --start-period=600s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8006/health', timeout=4)"

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8006"]
