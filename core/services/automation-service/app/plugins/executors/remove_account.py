"""Executor: remove a managed account from target hosts."""

from __future__ import annotations

import asyncio
from typing import Callable

from libs.pluginbase import ActionExecutor, RunRequest, RunResult

from app._account_ops import run_account_op


class RemoveAccountExecutor(ActionExecutor):
    name = "remove_account"

    def validate(self, params: dict) -> None:
        if not params.get("subject_credential_id") and not params.get("subject_cred_id"):
            raise ValueError("remove_account requires subject_credential_id in params")

    async def execute(self, request: RunRequest, publish_line: Callable) -> RunResult:
        return await run_account_op(request, publish_line, op="remove")

    async def run(self, request: RunRequest) -> RunResult:
        return await self.execute(request, lambda _: asyncio.sleep(0))
