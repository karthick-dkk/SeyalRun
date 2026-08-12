"""Pluggable log-shipping backends + connection tests (v1.1 Feature 1).

Backends accept already-redacted structured entries (the securelog filter runs first).
Connection tests are used by the admin LogBackend UI. Everything imports its heavy deps
lazily so services that don't ship logs don't pay for boto3/httpx at import time."""

from __future__ import annotations

import gzip
import json
import time
from typing import Any


# ── Connection tests ─────────────────────────────────────────────────────────

async def test_elasticsearch(es_url: str, es_api_key: str = "", timeout: float = 6.0,
                             verify_ssl: bool = True) -> dict:
    if not es_url:
        return {"ok": False, "latency_ms": 0, "error": "es_url is empty"}
    import httpx
    headers = {"Authorization": f"ApiKey {es_api_key}"} if es_api_key else {}
    start = time.monotonic()
    try:
        # verify_ssl=False lets an admin point at an on-prem ES with a self-signed cert
        # (the "certificate verify failed" case). It is the admin's explicit choice.
        async with httpx.AsyncClient(timeout=timeout, verify=verify_ssl) as client:
            r = await client.get(es_url.rstrip("/"), headers=headers)
        latency = int((time.monotonic() - start) * 1000)
        note = "" if verify_ssl else " (TLS certificate NOT verified)"
        if r.status_code < 400:
            return {"ok": True, "latency_ms": latency, "error": "", "note": note.strip()}
        return {"ok": False, "latency_ms": latency, "error": f"HTTP {r.status_code}"}
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if verify_ssl and "CERTIFICATE_VERIFY_FAILED" in msg:
            msg += "  — this ES uses a self-signed certificate; uncheck 'Verify TLS' to allow it."
        return {"ok": False, "latency_ms": int((time.monotonic() - start) * 1000), "error": msg}


def _s3_client(cfg: dict):
    import boto3
    kwargs: dict[str, Any] = {
        "aws_access_key_id": cfg.get("s3_access_key_id"),
        "aws_secret_access_key": cfg.get("s3_secret_access_key"),
    }
    if cfg.get("s3_region"):
        kwargs["region_name"] = cfg["s3_region"]
    if cfg.get("s3_endpoint_url"):
        kwargs["endpoint_url"] = cfg["s3_endpoint_url"]
    return boto3.client("s3", **kwargs)


async def test_s3(cfg: dict, timeout: float = 8.0) -> dict:
    """Test the actual capability log-shipping needs: WRITE. head_bucket requires
    s3:ListBucket and is region-strict (a common source of a 403 even when the write
    IAM policy is correct), so we put a tiny object under the real log prefix and delete
    it — exactly the permission (s3:PutObject) the backend uses."""
    import asyncio
    if not cfg.get("s3_bucket"):
        return {"ok": False, "latency_ms": 0, "error": "s3_bucket is empty"}
    start = time.monotonic()
    bucket = cfg["s3_bucket"]
    key = "logs/.seyalrun-connection-test"

    def _probe():
        c = _s3_client(cfg)
        c.put_object(Bucket=bucket, Key=key, Body=b"seyalrun-connection-test")
        try:
            c.delete_object(Bucket=bucket, Key=key)   # best-effort cleanup
        except Exception:  # noqa: BLE001
            pass

    try:
        await asyncio.wait_for(asyncio.get_event_loop().run_in_executor(None, _probe), timeout=timeout)
        return {"ok": True, "latency_ms": int((time.monotonic() - start) * 1000), "error": ""}
    except ModuleNotFoundError:
        return {"ok": False, "latency_ms": 0, "error": "boto3 not installed in this service"}
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        low = msg.lower()
        if "403" in msg or "accessdenied" in low or "forbidden" in low:
            msg += "  — the key needs s3:PutObject on arn:aws:s3:::" + bucket + "/*  (not just bucket-level perms)."
        elif "301" in msg or "permanentredirect" in low or "authorizationheadermalformed" in low:
            msg += "  — the bucket is in a different region; set Region to the bucket's actual region."
        return {"ok": False, "latency_ms": int((time.monotonic() - start) * 1000), "error": msg}


# ── Backends ─────────────────────────────────────────────────────────────────

class LocalDBBackend:
    name = "local"

    async def write_batch(self, entries: list[dict]) -> None:  # logs already on local volume
        return None


class ElasticsearchBackend:
    name = "elasticsearch"

    def __init__(self, es_url: str, es_api_key: str = "", index_prefix: str = "seyalrun",
                 verify_ssl: bool = True):
        self.es_url = es_url.rstrip("/")
        self.es_api_key = es_api_key
        self.index_prefix = index_prefix
        self.verify_ssl = verify_ssl

    async def write_batch(self, entries: list[dict]) -> None:
        if not entries or not self.es_url:
            return
        import httpx
        index = f"{self.index_prefix}-{time.strftime('%Y.%m.%d')}"
        lines = []
        for e in entries:
            lines.append(json.dumps({"index": {"_index": index}}))
            lines.append(json.dumps(e))
        body = "\n".join(lines) + "\n"
        headers = {"Content-Type": "application/x-ndjson"}
        if self.es_api_key:
            headers["Authorization"] = f"ApiKey {self.es_api_key}"
        async with httpx.AsyncClient(timeout=10, verify=self.verify_ssl) as client:
            r = await client.post(f"{self.es_url}/_bulk", content=body, headers=headers)
        # Raise on transport-level failure (401/403/5xx) so the shipper does NOT
        # advance the file offset and retries — otherwise a bad API key silently
        # drops logs. _bulk also returns 200 with per-item errors on partial
        # failure, so inspect the body's top-level "errors" flag too.
        if r.status_code >= 400:
            raise RuntimeError(f"elasticsearch _bulk HTTP {r.status_code}: {r.text[:200]}")
        try:
            if r.json().get("errors"):
                raise RuntimeError(f"elasticsearch _bulk partial failure: {r.text[:200]}")
        except ValueError:
            pass  # non-JSON 2xx body — treat as success


class S3Backend:
    name = "s3"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    async def write_batch(self, entries: list[dict]) -> None:
        if not entries or not self.cfg.get("s3_bucket"):
            return
        import asyncio
        key = f"logs/{time.strftime('%Y/%m/%d')}/{int(time.time()*1000)}.ndjson.gz"
        body = gzip.compress(("\n".join(json.dumps(e) for e in entries) + "\n").encode())

        def _put():
            _s3_client(self.cfg).put_object(Bucket=self.cfg["s3_bucket"], Key=key, Body=body)
        await asyncio.get_event_loop().run_in_executor(None, _put)


class CompositeBackend:
    name = "es+s3"

    def __init__(self, *backends):
        self.backends = backends

    async def write_batch(self, entries: list[dict]) -> None:
        for b in self.backends:
            try:
                await b.write_batch(entries)
            except Exception:  # noqa: BLE001
                pass


def build_backend(backend: str, cfg: dict):
    """Construct the shipping backend from a DECRYPTED config dict. `backend` is
    local|elasticsearch|s3|es+s3. Returns None for local/unknown (nothing to ship)."""
    es = ElasticsearchBackend(cfg.get("es_url", ""), cfg.get("es_api_key", ""),
                              cfg.get("es_index_prefix", "seyalrun"),
                              verify_ssl=cfg.get("es_verify_ssl", True))
    s3 = S3Backend(cfg)
    if backend == "elasticsearch":
        return es
    if backend == "s3":
        return s3
    if backend == "es+s3":
        return CompositeBackend(es, s3)
    return None
