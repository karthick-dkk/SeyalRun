"""Generate valid Ansible YAML from a list of TaskDefinition objects.

Uses ruamel.yaml for comment-preserving, round-trip safe output.
Input comes from validated Pydantic models — never raw user YAML strings.
"""

from __future__ import annotations

import io
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from ..models.schemas import TaskDefinition, VariableDefinition


def build_yaml(
    playbook_name: str,
    tasks: list[TaskDefinition],
    variables: list[VariableDefinition] | None = None,
    description: str = "",
    target_hosts: str = "all",
    gather_facts: bool = True,
    become: bool = False,
) -> str:
    """Generate a complete Ansible playbook YAML string from task definitions.

    Returns a valid YAML string ready to save to disk and execute.
    """
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.best_map_flow_style = False
    yaml.width = 120
    yaml.indent(mapping=2, sequence=4, offset=2)

    # Build vars dict from VariableDefinition list
    play_vars: dict[str, Any] = {}
    if variables:
        for v in variables:
            play_vars[v.name] = v.default

    # Build the play structure
    play = CommentedMap()
    play["name"] = playbook_name
    play["hosts"] = target_hosts
    play["become"] = become
    play["gather_facts"] = gather_facts

    if description:
        play.yaml_set_comment_before_after_key("name", before=f"# {description}")

    if play_vars:
        play["vars"] = play_vars

    # Build task list
    task_list = CommentedSeq()
    for task in tasks:
        t = CommentedMap()
        t["name"] = task.name

        # Add module with params
        if task.params:
            module_params = CommentedMap(task.params)
            t[task.module] = module_params
        else:
            t[task.module] = None

        if task.become:
            t["become"] = True
        if task.when:
            t["when"] = task.when
        if task.register:
            t["register"] = task.register
        if task.ignore_errors:
            t["ignore_errors"] = True
        if task.loop is not None:
            t["loop"] = task.loop
        if task.loop_control:
            t["loop_control"] = task.loop_control
        if task.delegate_to:
            t["delegate_to"] = task.delegate_to
        if task.no_log is not None:
            t["no_log"] = task.no_log
        if task.notify:
            t["notify"] = task.notify
        if task.tags:
            t["tags"] = task.tags

        task_list.append(t)

    play["tasks"] = task_list

    # Wrap in play list
    playbook = [play]

    # Render to string
    buf = io.StringIO()
    buf.write("---\n")
    yaml.dump(playbook, buf)
    return buf.getvalue()


def validate_task_definitions(tasks: list[TaskDefinition]) -> list[str]:
    """Return a list of validation warnings (not errors) for task definitions."""
    warnings = []
    seen_registers = set()

    for i, task in enumerate(tasks, 1):
        if not task.name:
            warnings.append(f"Task {i}: name is empty")

        if not task.module:
            warnings.append(f"Task {i} '{task.name}': no module specified")

        if task.register and task.register in seen_registers:
            warnings.append(
                f"Task {i} '{task.name}': register variable '{task.register}' already used"
            )
        if task.register:
            seen_registers.add(task.register)

        # Warn on shell with no-op content
        if task.module == "ansible.builtin.shell" and not task.params.get("cmd"):
            warnings.append(f"Task {i} '{task.name}': shell module missing 'cmd' parameter")

    return warnings
