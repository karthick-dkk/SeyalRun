"""Executor: run a template that's just an ordered list of other templates.

Each step is a fully-configured, pre-existing job template (own script, own
targets, own credential) — this executor doesn't accept any of a step's own
runtime params, it just replays that step template's own defaults, exactly
like a normal top-level run of it would.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Callable

from libs.pluginbase import ActionExecutor, RunRequest, RunResult


class ChainExecutor(ActionExecutor):
    name = "chain"

    def validate(self, params: dict) -> None:
        pass  # chain_steps lives on the template row, not caller params

    async def execute(self, request: RunRequest, publish_line: Callable) -> RunResult:
        from app._params import template_code_params
        from app._production_gate import any_production_hosts
        from app.database import SessionLocal
        from app.models import ZAJobRun, ZAJobTemplate
        from app import runner as _runner
        from libs.pluginbase import discover_plugins

        steps = request.params.get("_chain_steps") or []
        parent_run_id = request.params.get("_run_id")
        if not steps:
            return RunResult(ok=False, output="chain has no steps configured", exit_code=1)

        # PCI DSS Phase D — production is gated on every other dispatch path
        # (manual run, cron schedule, Zabbix webhook); a chain step is awaited
        # synchronously as part of one continuous run, so it can't cleanly land
        # in pending_approval and resume later without a real suspend/resume
        # mechanism this executor doesn't have. Conservative fix instead: refuse
        # the WHOLE chain up front if ANY step targets a production host, rather
        # than silently running some/all steps unapproved. The human must run
        # that step individually (through run_template's own approval gate) or
        # remove the production host from the chain.
        async with SessionLocal() as session:
            step_templates = {
                step.get("template_id"): await session.get(ZAJobTemplate, step.get("template_id"))
                for step in steps
            }
        all_step_host_ids = [
            hid
            for tmpl in step_templates.values() if tmpl is not None
            for hid in (tmpl.target_host_ids or [])
        ]
        if await any_production_hosts(all_step_host_ids):
            await publish_line(
                "[error] this chain has a step targeting a production-tagged host — "
                "chains cannot run against production; run that step individually "
                "so it goes through the normal approval flow"
            )
            return RunResult(ok=False, output="chain targets a production host — blocked", exit_code=1)

        # Built lazily (not at module import time) — this package is still being
        # discovered the first time app.state.executors is built, and this file
        # is itself one of its members.
        executors = discover_plugins("app.plugins.executors", ActionExecutor)

        overall_ok = True
        for i, step in enumerate(steps, start=1):
            step_tmpl_id = step.get("template_id")
            continue_on_failure = bool(step.get("continue_on_failure"))
            await publish_line(f"=== step {i}/{len(steps)}: template {step_tmpl_id} ===")

            async with SessionLocal() as session:
                step_tmpl = await session.get(ZAJobTemplate, step_tmpl_id)

            if step_tmpl is None:
                await publish_line(f"[error] step {i}: template {step_tmpl_id} not found")
                overall_ok = False
                if not continue_on_failure:
                    break
                continue
            if not step_tmpl.enabled:
                await publish_line(f"[error] step {i}: template '{step_tmpl.name}' is disabled")
                overall_ok = False
                if not continue_on_failure:
                    break
                continue
            # A chain step can never itself be a chain — no nested chains. Keeps
            # this simple (no cycle detection needed) and matches the plan's own
            # scope: a chain is a flat, ordered list of "real" action templates.
            if step_tmpl.action_type == "chain":
                await publish_line(f"[error] step {i}: '{step_tmpl.name}' is itself a chain — nested chains aren't supported")
                overall_ok = False
                if not continue_on_failure:
                    break
                continue

            step_executor = executors.get(step_tmpl.action_type)
            if step_executor is None:
                await publish_line(f"[error] step {i}: no executor for action_type={step_tmpl.action_type}")
                overall_ok = False
                if not continue_on_failure:
                    break
                continue

            step_run_id = str(uuid.uuid4())
            target_host_ids = list(step_tmpl.target_host_ids or [])
            stored_params = {
                **step_tmpl.default_params,
                "_target_host_ids": target_host_ids,
                "_target_host_group_id": step_tmpl.target_host_group_id,
                "_credential_mode": "default",
                "_credential_id": step_tmpl.credential_id,
                "_host_credentials": {},
                "_dry_run": False,
                "_max_parallel": step_tmpl.max_parallel,
                "_forks": step_tmpl.forks,
            }

            async with SessionLocal() as session:
                child_run = ZAJobRun(
                    id=step_run_id,
                    job_template_id=step_tmpl.id,
                    triggered_by=request.triggered_by or "",
                    status="pending",
                    params=stored_params,
                    output_lines=[],
                    parent_run_id=parent_run_id,
                )
                session.add(child_run)
                await session.commit()

            step_req = RunRequest(
                action_type=step_tmpl.action_type,
                target_host_ids=target_host_ids,
                credential_id=step_tmpl.credential_id,
                params={**stored_params, **template_code_params(step_tmpl)},
                triggered_by=request.triggered_by,
            )
            # Awaited directly (not asyncio.create_task) — steps run strictly one
            # after another, and this step's own runner.execute() call already
            # gives it its own real ZAJobRun lifecycle (status/output/notification).
            await _runner.execute(
                step_run_id, step_executor, step_req,
                timeout_seconds=step_tmpl.timeout_seconds, template_name=step_tmpl.name,
                retry_count=step_tmpl.retry_count, retry_delay_seconds=step_tmpl.retry_delay_seconds,
            )

            async with SessionLocal() as session:
                finished = await session.get(ZAJobRun, step_run_id)
            step_ok = finished is not None and finished.status == "success"
            await publish_line(f"[step {i}] {step_tmpl.name}: {finished.status if finished else 'error'}")
            if not step_ok:
                overall_ok = False
                if not continue_on_failure:
                    await publish_line(f"[chain] stopping — step {i} failed and continue_on_failure is not set")
                    break

        return RunResult(ok=overall_ok, output="", exit_code=0 if overall_ok else 1)

    async def run(self, request: RunRequest) -> RunResult:
        return await self.execute(request, lambda _: asyncio.sleep(0))
