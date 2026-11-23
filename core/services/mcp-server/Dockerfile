# Build context: repo root
FROM python:3.12-slim AS build
WORKDIR /build
COPY services/mcp-server/requirements.txt .
RUN pip install --no-cache-dir --retries 10 --timeout 120 --target=/deps -r requirements.txt

FROM python:3.12-slim
RUN useradd -r -u 10001 -m appuser
WORKDIR /app
COPY --from=build /deps /usr/local/lib/python3.12/site-packages
# Only the one stdlib-only lib it needs (MCP settings shape/defaults) — not libs/*,
# so this internet-facing image carries no service-token or crypto code.
COPY libs/mcpsettings ./libs/mcpsettings
COPY services/mcp-server/app ./app
USER appuser
EXPOSE 8110
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8110"]
