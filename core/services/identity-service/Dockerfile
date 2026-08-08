# Build context: repo root (needs access to ../../libs)
FROM python:3.12-slim AS build
WORKDIR /build
COPY services/identity-service/requirements.txt .
RUN pip install --no-cache-dir --retries 10 --timeout 120 --target=/deps -r requirements.txt

FROM python:3.12-slim
RUN useradd -r -u 10001 -m appuser
WORKDIR /app
COPY --from=build /deps /usr/local/lib/python3.12/site-packages
COPY libs ./libs
COPY services/identity-service/app ./app
COPY services/identity-service/migrations ./migrations
COPY services/identity-service/alembic.ini ./alembic.ini
RUN mkdir -p /var/log/seyalrun && chown appuser:appuser /var/log/seyalrun
USER appuser
EXPOSE 8101
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8101"]
