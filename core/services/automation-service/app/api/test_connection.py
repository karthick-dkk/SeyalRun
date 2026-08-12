from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.deps import require_service_token
from app.plugins.executors.ansible_playbook import run_ansible_playbook

router = APIRouter(dependencies=[Depends(require_service_token)])

# Fixed "targets" group name matches build_inventory()'s hardcoded
# all.children.targets.hosts structure — no changes needed there.
_PING_PLAYBOOK = """- name: SeyalRun connectivity test
  hosts: targets
  gather_facts: false
  tasks:
    - name: ping
      ansible.builtin.ping:
"""
_TIMEOUT_S = 20.0


class TestConnectionRequest(BaseModel):
    host_id: str
    credential_id: str | None = None


class TestConnectionResult(BaseModel):
    ok: bool
    output: str
    exit_code: int | None = None


@router.post("/test-connection", response_model=TestConnectionResult)
async def test_connection(payload: TestConnectionRequest):
    """Synchronous Ansible-ping connectivity check — deliberately NOT routed
    through the job-run pipeline (no za_job_runs row, no Redis/WebSocket
    streaming, nothing in Recent Runs). A quick pass/fail check belongs in
    the dialog that asked for it, not the run history."""
    from app.config import get_settings
    import httpx
    from libs.servicetoken import mint

    settings = get_settings()
    tok = mint("automation-service", "inventory-service", settings.service_jwt_secret)

    async with httpx.AsyncClient(base_url=settings.inventory_service_url, timeout=10) as client:
        resp = await client.get(f"/api/v1/internal/hosts/{payload.host_id}",
                                headers={"X-Service-Token": tok})
        if resp.status_code != 200:
            return TestConnectionResult(ok=False, output=f"host lookup failed: {resp.status_code}")
        host = resp.json()

        cred_id = payload.credential_id
        if not cred_id:
            rr = await client.get("/api/v1/credentials", params={"host_id": payload.host_id},
                                  headers={"X-Service-Token": tok})
            if rr.status_code == 200 and rr.json():
                cred_id = rr.json()[0]["id"]   # is_default-ordered — see credentials.py fix
        if not cred_id:
            return TestConnectionResult(ok=False, output="no credential available to connect")

        cr = await client.get(f"/api/v1/internal/credentials/{cred_id}/secret",
                              headers={"X-Service-Token": tok})
        if cr.status_code != 200:
            return TestConnectionResult(ok=False, output="credential secret lookup failed")
        cred = cr.json()

    lines: list[str] = []

    async def _collect(line: str) -> None:
        lines.append(line)

    try:
        result = await asyncio.wait_for(
            run_ansible_playbook([(host, cred, None)], _PING_PLAYBOOK, _collect),
            timeout=_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        return TestConnectionResult(ok=False, output="\n".join(lines) + "\n[timeout] connection test exceeded 20s")

    return TestConnectionResult(ok=result.ok, output="\n".join(lines), exit_code=result.exit_code)
