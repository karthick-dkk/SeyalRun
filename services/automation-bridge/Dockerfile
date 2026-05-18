# syntax=docker/dockerfile:1
# Build for linux/arm64 (Server 1 is aarch64 / Apple Silicon VM)

ARG PYTHON_VERSION=3.12
ARG GIT_SHA=unknown

# ── Stage 1: Build dependencies ──────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

WORKDIR /build

# Install build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install shared lib first (changes rarely → better layer caching)
COPY ../../../_shared /shared
RUN pip install --no-cache-dir --prefix=/install /shared

# Install service dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir --prefix=/install .

# ── Stage 2: Runtime ─────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim-bookworm

ARG GIT_SHA
ENV GIT_SHA=${GIT_SHA} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Install ansible (required by ansible-runner at runtime)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ansible \
    openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get purge -y --auto-remove

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Create non-root user
RUN useradd -r -u 1001 -g 0 appuser \
    && mkdir -p /app /playbooks /run/certs \
    && chown -R appuser:0 /app /playbooks /run/certs

COPY --chown=appuser:0 app/ /app/app/

USER appuser

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')"

ENTRYPOINT ["python", "-m", "uvicorn", "app.main:app", \
    "--host", "0.0.0.0", "--port", "8001", "--no-access-log"]
