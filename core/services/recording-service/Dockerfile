# Build context: repo root (needs access to libs/)
FROM python:3.12-slim AS build
WORKDIR /build
COPY services/recording-service/requirements.txt .
RUN pip install --no-cache-dir --retries 10 --timeout 120 --target=/deps -r requirements.txt

FROM python:3.12-slim
RUN useradd -r -u 10001 -m appuser
WORKDIR /app
COPY --from=build /deps /usr/local/lib/python3.12/site-packages
COPY libs ./libs
COPY services/recording-service/app ./app
COPY services/recording-service/migrations ./migrations
COPY services/recording-service/alembic.ini ./alembic.ini
RUN mkdir -p /var/log/seyalrun && chown appuser:appuser /var/log/seyalrun
USER appuser
EXPOSE 8104
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8104"]
