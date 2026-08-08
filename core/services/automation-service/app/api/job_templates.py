from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app._params import ParamNotAllowedError, filter_caller_params, template_code_params
from app._production_gate import any_production_hosts as _any_production_hosts
from app.database import get_session
from app.deps import require_service_token, get_user_id, get_user_role
from app.models import ZAJobRun, ZAJobTemplate

router = APIRouter(dependencies=[Depends(require_service_token)])


class TemplateCreate(BaseModel):
    project_id: str
    name: str
    description: str = ""
    action_type: str
    playbook_path: str = ""
    script_content: str = ""
    target_scope: str = "hosts"
    target_host_ids: list[str] = []
    target_host_group_id: str | None = None
    credential_id: str | None = None
    subject_credential_id: str | None = None
    survey_schema: dict = {}
    default_params: dict = {}
    allowed_param_keys: list[str] = []
    quick_action: bool = False
    enabled: bool = True
    timeout_seconds: int | None = None
    retry_count: int = 0
    retry_delay_seconds: int = 30
    max_parallel: int = 1
    forks: int | None = None
    requires_approval: bool = False
    approver_role: str | None = None
    chain_steps: list[dict] = []


class TemplateUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    action_type: str | None = None
    playbook_path: str | None = None
    script_content: str | None = None
    target_scope: str | None = None
    target_host_ids: list[str] | None = None
    credential_id: str | None = None
    subject_credential_id: str | None = None
    quick_action: bool | None = None
    enabled: bool | None = None
    survey_schema: dict | None = None
    default_params: dict | None = None
    allowed_param_keys: list[str] | None = None
    timeout_seconds: int | None = None
    retry_count: int | None = None
    retry_delay_seconds: int | None = None
    max_parallel: int | None = None
    forks: int | None = None
    requires_approval: bool | None = None
    approver_role: str | None = None
    chain_steps: list[dict] | None = None


class PlaybookImportRequest(BaseModel):
    url: str


# SSRF guard for the GitHub playbook-import endpoint below: only these two hosts are ever
# fetched, redirects are never followed (a redirect response is just rejected as a non-200),
# and both the URL the caller supplied AND the raw URL it resolves to are checked — a
# malicious blob-URL-shaped string can't be crafted to resolve outside this allowlist.
_GITHUB_IMPORT_HOSTS = {"github.com", "raw.githubusercontent.com"}
_GITHUB_IMPORT_MAX_BYTES = 512_000


def _to_raw_github_url(url: str) -> str:
    """https://github.com/{owner}/{repo}/blob/{branch}/{path} -> the raw.githubusercontent.com
    equivalent. A URL that's already raw.githubusercontent.com passes through unchanged."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.hostname == "raw.githubusercontent.com":
        return url
    if parsed.hostname == "github.com":
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 5 and parts[2] == "blob":
            owner, repo, branch, rest = parts[0], parts[1], parts[3], "/".join(parts[4:])
            return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{rest}"
    raise ValueError("unsupported GitHub URL — use a github.com blob link (…/blob/branch/path) "
                     "or a raw.githubusercontent.com link")


class RunPayload(BaseModel):
    params: dict = {}
    target_host_ids: list[str] | None = None
    target_host_group_id: str | None = None  # recorded for history; frontend resolves members
    # credential_mode: "default" → host's push account; "all" → one credential_id for every
    # host; "per_host" → host_credentials map. Falls back to the template's pinned credential
    # for ansible/bash when "default" is chosen and the host has no linked credential.
    credential_mode: str = "default"
    credential_id: str | None = None
    host_credentials: dict[str, str] = {}
    # Ansible --check/--diff, requested per-run — every caller can always ask for the
    # safer path regardless of the template's own allowed_param_keys, so this is a
    # dedicated top-level field rather than something routed through params/the survey
    # allowlist. No effect on bash_script (no Ansible-equivalent exists).
    dry_run: bool = False


@router.get("/job-templates")
async def list_templates(
    quick_action: bool | None = None,
    session: AsyncSession = Depends(get_session),
):
    q = select(ZAJobTemplate).where(ZAJobTemplate.enabled.is_(True))
    if quick_action is not None:
        q = q.where(ZAJobTemplate.quick_action.is_(quick_action))
    result = await session.execute(q.order_by(ZAJobTemplate.created_at.desc()))
    return [_out(t) for t in result.scalars().all()]


@router.post("/job-templates", status_code=status.HTTP_201_CREATED)
async def create_template(
    payload: TemplateCreate,
    role: str = Depends(get_user_role),
    user_id: str = Depends(get_user_id),
    session: AsyncSession = Depends(get_session),
):
    if role not in ("superadmin", "admin", "automation"):
        raise HTTPException(status_code=403, detail="automation write access required")
    tmpl = ZAJobTemplate(**payload.model_dump(), created_by=user_id)
    session.add(tmpl)
    await session.commit()
    await session.refresh(tmpl)
    return _out(tmpl)


@router.post("/job-templates/import-playbook")
async def import_playbook(
    payload: PlaybookImportRequest,
    role: str = Depends(get_user_role),
):
    """Fetch playbook YAML text from a GitHub URL for the template editor's
    script_content field — this never persists anything, just returns text to prefill
    the form; the user still reviews and saves it like any manually-pasted playbook."""
    if role not in ("superadmin", "admin", "automation"):
        raise HTTPException(status_code=403, detail="automation write access required")

    from urllib.parse import urlparse

    parsed = urlparse(payload.url)
    if parsed.scheme != "https" or parsed.hostname not in _GITHUB_IMPORT_HOSTS:
        raise HTTPException(status_code=400,
                            detail="only https://github.com or https://raw.githubusercontent.com URLs are supported")
    try:
        raw_url = _to_raw_github_url(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    raw_parsed = urlparse(raw_url)
    if raw_parsed.scheme != "https" or raw_parsed.hostname not in _GITHUB_IMPORT_HOSTS:
        raise HTTPException(status_code=400, detail="resolved URL is not on an allowed host")

    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=10) as client:
            resp = await client.get(raw_url)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="could not reach GitHub") from exc

    if resp.status_code != 200:
        raise HTTPException(status_code=502,
                            detail=f"GitHub returned {resp.status_code} — check the URL and that the file/branch exists")
    if len(resp.content) > _GITHUB_IMPORT_MAX_BYTES:
        raise HTTPException(status_code=400, detail="file too large (500KB limit)")

    return {"content": resp.text, "source_url": raw_url}


@router.get("/job-templates/{template_id}")
async def get_template(template_id: str, session: AsyncSession = Depends(get_session)):
    tmpl = await session.get(ZAJobTemplate, template_id)
    if tmpl is None:
        raise HTTPException(status_code=404)
    return _out(tmpl)


@router.put("/job-templates/{template_id}")
async def update_template(
    template_id: str,
    payload: TemplateUpdate,
    role: str = Depends(get_user_role),
    session: AsyncSession = Depends(get_session),
):
    if role not in ("superadmin", "admin", "automation"):
        raise HTTPException(status_code=403, detail="automation write access required")
    tmpl = await session.get(ZAJobTemplate, template_id)
    if tmpl is None:
        raise HTTPException(status_code=404)
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(tmpl, k, v)
    tmpl.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(tmpl)
    return _out(tmpl)


@router.delete("/job-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: str,
    role: str = Depends(get_user_role),
    session: AsyncSession = Depends(get_session),
):
    if role not in ("superadmin", "admin", "automation"):
        raise HTTPException(status_code=403, detail="automation write access required")
    tmpl = await session.get(ZAJobTemplate, template_id)
    if tmpl is None:
        raise HTTPException(status_code=404)
    await session.delete(tmpl)
    await session.commit()


@router.post("/job-templates/{template_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_template(
    template_id: str,
    payload: RunPayload,
    request: Request,
    user_id: str = Depends(get_user_id),
    session: AsyncSession = Depends(get_session),
):
    tmpl = await session.get(ZAJobTemplate, template_id)
    if tmpl is None:
        raise HTTPException(status_code=404)
    if not tmpl.enabled:
        raise HTTPException(status_code=400, detail="template is disabled")

    target_host_ids = payload.target_host_ids or list(tmpl.target_host_ids or [])
    # Allowlist caller-supplied survey params; script_content/playbook_path come from
    # the template only (same guard as the webhook path).
    try:
        caller_params = filter_caller_params(tmpl, payload.params)
    except ParamNotAllowedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    params = {**tmpl.default_params, **caller_params}

    # Runtime Variables (survey_schema.fields): validate each declared field's supplied
    # value against its own rule — a string field's regex, or a dropdown's fixed option
    # list — the same "never silently proceed on bad/missing input" discipline already
    # applied to host/credential resolution elsewhere in this service. The UI enforces
    # these too, but a direct API caller must be held to the same bar.
    survey_fields = [f for f in (tmpl.survey_schema or {}).get("fields") or [] if isinstance(f, dict) and f.get("name")]
    if survey_fields:
        import re as _re

        for f in survey_fields:
            name = f["name"]
            val = str(params.get(name, f.get("default", "")))
            # Every declared Runtime Variable is mandatory — a blank value (no caller
            # override and no template default) must hard-fail here rather than let the
            # job run with an empty/missing input silently baked into $1, $2, ... or an
            # extra-var.
            if not val.strip():
                raise HTTPException(status_code=400, detail=f"'{f.get('prompt') or name}' is required")
            if f.get("type") == "dropdown":
                options = [str(o) for o in (f.get("options") or [])]
                if options and val not in options:
                    raise HTTPException(status_code=400, detail=f"'{f.get('prompt') or name}' must be one of: {', '.join(options)}")
            else:
                pattern = f.get("validation")
                if pattern:
                    try:
                        matches = _re.fullmatch(pattern, val) is not None
                    except _re.error:
                        matches = True  # malformed admin-authored regex — don't hard-fail the run over it
                    if not matches:
                        raise HTTPException(status_code=400, detail=f"'{f.get('prompt') or name}' does not match the required pattern")
            params[name] = val

        # bash_script has no named-variable mechanism of its own — positional $1 $2 ...
        # only. Build that positional string here, in the fields' declared order, so the
        # executor's existing script_args -> shlex pipeline (bash_script.py) needs no
        # changes: shlex.quote makes each value exactly one token regardless of spaces,
        # and the executor's own shlex.split/shlex.quote round-trip that safely.
        if tmpl.action_type == "bash_script":
            import shlex as _shlex

            params["script_args"] = " ".join(_shlex.quote(str(params.get(f["name"], ""))) for f in survey_fields)

    # Account operations act on a "subject" account (the user created/rotated/disabled/
    # removed on each host). Fall back to the template's pinned subject credential when the
    # run didn't specify one, so the template editor's "Subject Credential" actually applies.
    if tmpl.action_type in ("account_push", "rotate_secret", "disable_account", "remove_account"):
        if not params.get("subject_credential_id") and tmpl.subject_credential_id:
            params["subject_credential_id"] = tmpl.subject_credential_id

    # Resolve the credential selection into an effective single credential + per-host map.
    mode = payload.credential_mode or "default"
    if mode == "all":
        eff_credential_id = payload.credential_id
        host_creds: dict[str, str] = {}
    elif mode == "per_host":
        eff_credential_id = None
        host_creds = payload.host_credentials or {}
    else:  # "default" → use each host's push account (resolved in the executor)
        eff_credential_id = None
        host_creds = {}
        # ansible/bash need a login; if the host has no linked credential the template's
        # pinned credential is the sensible fallback so those templates still run.
        if tmpl.action_type in ("ansible_playbook", "bash_script"):
            eff_credential_id = tmpl.credential_id

    force_approval = await _any_production_hosts(target_host_ids)

    run_id = str(uuid.uuid4())
    stored_params = {
        **params,
        # _-prefixed keys are stored for re-run/history and are stripped from ansible
        # extra-vars by the executor.
        "_target_host_ids": target_host_ids,
        "_target_host_group_id": payload.target_host_group_id,
        "_credential_mode": mode,
        "_credential_id": eff_credential_id,
        "_host_credentials": host_creds,
        "_dry_run": payload.dry_run,
        "_max_parallel": tmpl.max_parallel,
        "_forks": tmpl.forks,
        # Only meaningful for action_type == "chain" (ChainExecutor) — the step
        # list to run in order, and this run's own id so each step's child
        # ZAJobRun can record it as parent_run_id.
        "_chain_steps": tmpl.chain_steps if tmpl.action_type == "chain" else None,
        "_run_id": run_id,
    }
    run = ZAJobRun(
        id=run_id,
        job_template_id=template_id,
        triggered_by=f"user:{user_id}",
        # A template with requires_approval gets a real ZAJobRun row (visible in
        # history/notifications) but no dispatch — the run only starts once an
        # approver-role user calls POST /job-runs/{id}/approve (see job_runs.py).
        # force_approval (any target host tagged is_production) overrides the
        # template's own setting the same way — production is always gated.
        status="pending_approval" if (tmpl.requires_approval or force_approval) else "pending",
        params=stored_params,
        output_lines=[],
    )
    session.add(run)
    await session.commit()

    if tmpl.requires_approval or force_approval:
        return {"run_id": run_id, "status": "pending_approval"}

    executors = getattr(request.app.state, "executors", {})
    executor = executors.get(tmpl.action_type)
    if executor is None:
        raise HTTPException(status_code=400, detail=f"no executor for action_type={tmpl.action_type}")

    from libs.pluginbase import RunRequest
    from app import runner as _runner

    req = RunRequest(
        action_type=tmpl.action_type,
        target_host_ids=target_host_ids,
        credential_id=eff_credential_id,
        params={**stored_params, **template_code_params(tmpl)},
        triggered_by=f"user:{user_id}",
    )
    asyncio.create_task(_runner.execute(
        run_id, executor, req, timeout_seconds=tmpl.timeout_seconds, template_name=tmpl.name,
        retry_count=tmpl.retry_count, retry_delay_seconds=tmpl.retry_delay_seconds,
    ))
    return {"run_id": run_id}


def _out(t: ZAJobTemplate) -> dict:
    return {
        "id": t.id, "project_id": t.project_id, "name": t.name,
        "description": t.description, "action_type": t.action_type,
        "playbook_path": t.playbook_path, "script_content": t.script_content,
        "target_scope": t.target_scope, "target_host_ids": t.target_host_ids,
        "target_host_group_id": t.target_host_group_id,
        "credential_id": t.credential_id, "subject_credential_id": t.subject_credential_id,
        "survey_schema": t.survey_schema, "default_params": t.default_params,
        "allowed_param_keys": list(t.allowed_param_keys or []),
        "quick_action": t.quick_action, "enabled": t.enabled, "created_by": t.created_by,
        "timeout_seconds": t.timeout_seconds, "retry_count": t.retry_count,
        "retry_delay_seconds": t.retry_delay_seconds, "max_parallel": t.max_parallel,
        "forks": t.forks, "requires_approval": t.requires_approval,
        "approver_role": t.approver_role, "chain_steps": list(t.chain_steps or []),
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }
