# Build context: repo root (needs access to libs/)
FROM python:3.12-slim AS build
WORKDIR /build
COPY services/posture-service/requirements.txt .
RUN pip install --no-cache-dir --retries 10 --timeout 120 --target=/deps -r requirements.txt

FROM python:3.12-slim
RUN useradd -r -u 10001 -m appuser
WORKDIR /app
COPY --from=build /deps /usr/local/lib/python3.12/site-packages
COPY libs ./libs
COPY services/posture-service/app ./app
COPY services/posture-service/migrations ./migrations
COPY services/posture-service/alembic.ini ./alembic.ini
COPY services/posture-service/tools ./tools
# /var/lib/seyalrun/posture is a named volume in compose (feed staging + upload
# spool — the root fs is read-only and /tmp is RAM). Created here so a fresh
# volume inherits appuser ownership.
RUN mkdir -p /var/log/seyalrun /var/lib/seyalrun/posture/uploads /var/lib/seyalrun/posture/staging \
    && chown -R appuser:appuser /var/log/seyalrun /var/lib/seyalrun
USER appuser
EXPOSE 8108
# One worker: the facts poller and in-process import tasks assume a single
# process, and the staging box is small.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8108", "--workers", "1"]
