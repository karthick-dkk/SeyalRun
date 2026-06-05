FROM python:3.12-slim-bookworm AS builder
WORKDIR /build
COPY _shared /shared
RUN pip install --no-cache-dir --prefix=/install /shared
COPY automation-bridge/pyproject.toml .
RUN pip install --no-cache-dir --prefix=/install ansible

FROM python:3.12-slim-bookworm
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA} PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
RUN apt-get update && apt-get install -y --no-install-recommends \
    ansible openssh-client curl && rm -rf /var/lib/apt/lists/*
COPY --from=builder /install /usr/local
RUN pip install --no-cache-dir fastapi uvicorn[standard] ansible-runner slowapi redis httpx tenacity pydantic pydantic-settings structlog prometheus-client
RUN useradd -r -u 1001 -g 0 appuser && mkdir -p /app /playbooks && chown appuser /app /playbooks
COPY --chown=appuser automation-bridge/app/ /app/app/
USER appuser
EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s CMD curl -f http://localhost:8001/health || exit 1
ENTRYPOINT ["python","-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8001","--no-access-log"]
