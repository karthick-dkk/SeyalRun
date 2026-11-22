"""Executor: collect read-only Linux host facts for posture-service.

The script is a fixed file shipped in this image (posture_facts.sh next to this
module), never taken from the template or the caller. posture-service can ask
for a facts run on a host, but it cannot choose what runs there: holding a
service token for this endpoint is not a way to execute arbitrary commands on
every managed host.

Everything else — inventory lookup, vault credential resolution, the SSH path,
sudo handling, output masking, the production approval gate, audit rows — is
the ordinary bash_script machinery, so posture collection inherits the same
controls as any other job instead of growing a second way to reach a host.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from libs.pluginbase import RunRequest, RunResult

from app.plugins.executors.bash_script import BashScriptExecutor

FACTS_SCRIPT = (Path(__file__).with_name("posture_facts.sh")).read_text()

# Caller/template params that would change WHAT runs or WHERE. Stripped so the
# fixed script always runs remotely, with no positional arguments.
_CODE_KEYS = ("script_content", "inline_script", "script_args", "run_local")


class PostureFactsExecutor(BashScriptExecutor):
    name = "posture_facts"
    # A fleet-wide collection is one run per host; a success notification for
    # each would bury the inbox. Failures still notify (see runner.py).
    notify_on_success = False

    def validate(self, params: dict) -> None:  # the script is fixed; nothing to check
        return None

    async def execute(self, request: RunRequest, publish_line: Callable) -> RunResult:
        params = {k: v for k, v in (request.params or {}).items() if k not in _CODE_KEYS}
        params["script_content"] = FACTS_SCRIPT
        params["run_local"] = False
        return await super().execute(replace(request, params=params), publish_line)
