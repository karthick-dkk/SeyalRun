# Build context: repo root (needs access to libs/)
FROM python:3.12-slim AS build
WORKDIR /build
COPY services/automation-service/requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client sshpass && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir --retries 10 --timeout 120 --target=/deps -r requirements.txt

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client sshpass && rm -rf /var/lib/apt/lists/*
RUN useradd -r -u 10001 -m appuser
WORKDIR /app
COPY --from=build /deps /usr/local/lib/python3.12/site-packages
# Console scripts from `pip install --target` land in /deps/bin (ansible-playbook,
# ansible, etc.) — put them on PATH so the executor can invoke ansible-playbook.
COPY --from=build /deps/bin /usr/local/bin
COPY libs ./libs
COPY services/automation-service/app ./app
COPY services/automation-service/migrations ./migrations
COPY services/automation-service/alembic.ini ./alembic.ini
RUN mkdir -p /var/log/seyalrun && chown appuser:appuser /var/log/seyalrun
USER appuser
EXPOSE 8105
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8105"]
