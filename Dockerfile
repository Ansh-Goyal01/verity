# One container for the FastAPI service (guide W5.3). Secrets come from the host's environment, never the image:
# SARVAM_API_KEY, NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD, VERITY_CORS_ORIGINS, VERITY_TRUST_PROXY (see docs/DEPLOY.md).
# Python 3.13 is the version the pinned demo numbers were produced on (CI uses it too).
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VERITY_ENV=production \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

RUN pip install --no-cache-dir uv==0.13.6

WORKDIR /app

# The project is installed editable, so the engine finds data/, results/ and docs/ next to it (engine/verity/paths.py).
COPY pyproject.toml uv.lock README.md ./
COPY engine ./engine
COPY api ./api
RUN uv sync --locked --no-dev

COPY data ./data
COPY results ./results

# Not root; data/cache is the one place the service writes (route and weather caches).
RUN useradd --system --no-create-home verity \
    && mkdir -p /app/data/cache \
    && chown -R verity /app/data/cache
USER verity

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD ["/app/.venv/bin/python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT', '8000'), timeout=4)"]

# Hosts that assign a port set $PORT; otherwise 8000.
CMD ["sh", "-c", "exec /app/.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-server-header"]
