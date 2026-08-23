"""AutomationView's modals moved out, and kept their behaviour.

The view mixed page state with five embedded modals, each owning its own form,
validation, request and error. Increments land on top of that file, so the
modals with a clean boundary come out first.

A "no behaviour change" refactor's risk is that behaviour changes, so these pin
the parts that are easy to lose when state moves across a component boundary:

  * defaults that were pre-filled stay pre-filled (a new schedule starts from a
    working cron, not an empty box the user has to decode);
  * forms reset on OPEN, not on close — a stale form means the next "New" opens
    pre-filled with the last edit, which is how someone overwrites a schedule
    they meant to create;
  * the parent keeps no state the child now owns, because a `loading` flag the
    parent can no longer update is a button stuck on its idle label forever;
  * children do not reach into the parent's reactive objects, which is how a
    1,700-line view gets that way.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "services/frontend/src"
VIEW = SRC / "views/AutomationView.vue"
GITHUB = SRC / "components/automation/GithubImportModal.vue"
SCHEDULE = SRC / "components/automation/ScheduleModal.vue"


def test_components_exist_and_view_shrank():
    assert GITHUB.exists() and SCHEDULE.exists()
    lines = len(VIEW.read_text().splitlines())
    assert lines < 1729, f"view is {lines} lines — the extraction did not land"


@pytest.mark.parametrize("component", [GITHUB, SCHEDULE])
def test_extracted_modals_are_used_not_orphaned(component: Path):
    """A component nobody renders is dead code that still passes a build."""
    name = component.stem
    src = VIEW.read_text()
    assert f"import {name} from" in src, f"{name} is not imported"
    assert f"<{name}" in src, f"{name} is imported but never rendered"


@pytest.mark.parametrize("component", [GITHUB, SCHEDULE])
def test_modals_do_not_reach_into_parent_state(component: Path):
    """The originals mutated editDlg/schedDlg directly. A child writing into a
    parent's reactive object is exactly how this file reached 1,700 lines."""
    src = component.read_text()
    for parent_obj in ("editDlg", "schedDlg", "runDlg", "allTemplates."):
        assert parent_obj not in src, f"{component.name} still touches parent state: {parent_obj}"


def test_view_keeps_no_state_the_child_now_owns():
    """A `loading` flag the parent cannot update leaves a button stuck on its
    idle label — dead state that looks live."""
    src = VIEW.read_text()
    decl = re.search(r"const githubImport = reactive\(\{([^}]*)\}\)", src)
    assert decl, "githubImport state not found"
    fields = set(re.findall(r"(\w+):", decl.group(1)))
    assert fields == {"visible"}, f"parent still holds child state: {fields - {'visible'}}"

    decl = re.search(r"const schedDlg = reactive<[^>]*>\(\{([^}]*)\}\)", src)
    assert decl, "schedDlg state not found"
    fields = set(re.findall(r"(\w+):", decl.group(1)))
    assert fields == {"visible", "editing"}, f"parent still holds child state: {fields}"


def test_new_schedule_still_prefills_the_cron_default():
    """It was '0 2 * * *'. An empty box is a different, worse form."""
    m = re.search(r"cron_expression:\s*'([^']*)'", SCHEDULE.read_text())
    assert m and m.group(1) == "0 2 * * *", f"cron default is {m and m.group(1)!r}"


def test_schedule_form_resets_on_open_not_on_close():
    src = SCHEDULE.read_text()
    watcher = src[src.index("watch(() => props.modelValue"):]
    watcher = watcher[: watcher.index("})", watcher.index("{"))]
    assert "if (!open) return" in watcher, (
        "resetting on close leaves the next 'New Schedule' pre-filled with the last edit"
    )
    assert "Object.assign(form, blank()" in watcher


def test_schedule_validation_survived_the_move():
    """All three required-field checks, not just the first."""
    src = SCHEDULE.read_text()
    for msg in ("Name required.", "Job template required.", "Cron expression required."):
        assert msg in src, f"lost validation: {msg}"


def test_github_import_still_detects_script_type():
    """Importing a .sh while 'Ansible Playbook' was selected ran the YAML parser
    over real bash and reported false syntax errors. The detection must move with
    the modal."""
    src = GITHUB.read_text()
    assert re.search(r"\\\.\(sh\|bash\)", src), "extension detection lost"
    assert "bash_script" in src and "ansible_playbook" in src


def test_github_modal_clears_stale_errors_on_reopen():
    """Old error text reappearing reads as a failure that has not happened yet."""
    src = GITHUB.read_text()
    assert "watch(() => props.modelValue" in src
    assert "error.value = ''" in src


def test_parent_reloads_after_a_schedule_is_saved():
    """The child cannot refresh the parent's list; it says so and the parent acts."""
    assert "@saved=\"loadAll\"" in VIEW.read_text()
    assert "emit('saved')" in SCHEDULE.read_text()


def test_github_import_result_is_applied_by_the_parent():
    src = VIEW.read_text()
    assert "@imported=\"onGithubImported\"" in src
    fn = src[src.index("function onGithubImported"):]
    fn = fn[: fn.index("\n}") + 2]
    for field in ("script_content", "imported_from", "action_type"):
        assert field in fn, f"the parent drops {field} from the import result"
