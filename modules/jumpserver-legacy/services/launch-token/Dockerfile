FROM python:3.12-slim-bookworm AS builder
WORKDIR /build
COPY ../../_shared /shared
RUN pip install --no-cache-dir --prefix=/install /shared
COPY pyproject.toml .
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim-bookworm
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA} PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY --from=builder /install /usr/local
RUN useradd -r -u 1001 -g 0 appuser && mkdir -p /app && chown appuser /app
COPY --chown=appuser app/ /app/app/
USER appuser
EXPOSE 8003
HEALTHCHECK --interval=30s --timeout=5s CMD curl -f http://localhost:8003/health || exit 1
ENTRYPOINT ["python","-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8003","--no-access-log"]
