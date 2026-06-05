FROM python:3.12-slim AS base

LABEL org.opencontainers.image.title="Playbook Studio"
LABEL org.opencontainers.image.description="Visual Ansible Playbook Builder — SeyalRun"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    openssh-client \
    sshpass \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (layer cache)
COPY pyproject.toml ./
RUN pip install --no-cache-dir ansible-core asyncssh

# Install Ansible collections needed by templates
# ansible.posix: firewalld, authorized_key
# community.general: ufw, timezone, filesystem
# community.crypto: x509_certificate, openssl_certificate_info
RUN ansible-galaxy collection install \
    ansible.posix \
    community.general \
    community.crypto \
    --no-deps 2>/dev/null || \
    ansible-galaxy collection install ansible.posix community.general community.crypto

RUN pip install --no-cache-dir -e ".[prod]" 2>/dev/null || \
    pip install --no-cache-dir \
        fastapi \
        "uvicorn[standard]" \
        slowapi \
        "redis[asyncio]" \
        httpx \
        "sqlalchemy[asyncio]" \
        asyncpg \
        alembic \
        "pydantic>=2.8" \
        pydantic-settings \
        ruamel.yaml \
        aiosmtplib \
        tenacity \
        websockets \
        structlog \
        prometheus-client

COPY . .

RUN chmod +x entrypoint.sh

EXPOSE 8005

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8005/health || exit 1

ENTRYPOINT ["./entrypoint.sh"]
