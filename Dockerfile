# syntax=docker/dockerfile:1.7
# ---- frontend build stage ------------------------------------------------
FROM node:20-alpine AS web
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund --prefer-offline
COPY frontend ./
RUN npm run build

# ---- python runtime ------------------------------------------------------
FROM python:3.12-slim AS app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /srv

# Build deps for pandas/numpy wheels are usually unnecessary on slim,
# but a few system libs help (libgomp for sklearn-style numpy threading).
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# pyproject references README.md via `readme = "..."`, so hatchling
# wants both files present at build time.
COPY pyproject.toml README.md ./
RUN pip install --upgrade pip && pip install ".[openai,postgres]"

COPY app ./app
COPY --from=web /app/dist ./static

# Non-root user — the sandbox subprocess inherits this uid, so RLIMIT_NPROC
# is enforceable on Linux.
RUN useradd -u 10001 -m datachat \
 && mkdir -p /srv/data && chown -R datachat /srv
USER datachat

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=2).status==200 else 1)"

CMD ["uvicorn", "app.api.factory:app", "--host", "0.0.0.0", "--port", "8080"]
