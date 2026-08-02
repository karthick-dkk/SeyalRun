"""PCI DSS Phase D — change control: --check --diff output from ansible-playbook
must parse into a structured, per-host diff_summary (ZAJobRun.diff_summary),
not just sit as opaque text in output_lines.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def mod(load_module):
    return load_module(
        "services/automation-service/app/plugins/executors/ansible_playbook.py",
        "za_ansible_exec_diff",
    )


_SAMPLE_OUTPUT = """
PLAY [all] *********************************************************

TASK [Gathering Facts] ************************************************
ok: [web-01]
ok: [web-02]

TASK [Ensure config file is present] ***********************************
--- before: /etc/app/config.yml
+++ after: /etc/app/config.yml
@@ -1,3 +1,3 @@
-debug: true
+debug: false
 port: 8080
changed: [web-01]

TASK [Ensure config file is present] ***********************************
ok: [web-02]

PLAY RECAP **************************************************************
web-01                     : ok=2    changed=1    unreachable=0    failed=0
web-02                     : ok=2    changed=0    unreachable=0    failed=0
""".strip().splitlines()


def test_diff_block_attributed_to_correct_host(mod):
    collector = mod._DiffCollector()
    for line in _SAMPLE_OUTPUT:
        collector.feed(line)

    assert set(collector.by_host.keys()) == {"web-01"}
    entries = collector.by_host["web-01"]
    assert len(entries) == 1
    assert entries[0]["task"] == "Ensure config file is present"
    assert "-debug: true" in entries[0]["diff"]
    assert "+debug: false" in entries[0]["diff"]


def test_no_diff_blocks_when_nothing_changed(mod):
    collector = mod._DiffCollector()
    for line in ["TASK [noop] ***", "ok: [web-02]"]:
        collector.feed(line)
    assert collector.by_host == {}


def test_multiple_hosts_same_task_kept_separate(mod):
    collector = mod._DiffCollector()
    lines = [
        "TASK [Ensure config file is present] ***",
        "--- before: /etc/app/config.yml",
        "+++ after: /etc/app/config.yml",
        "-debug: true",
        "+debug: false",
        "changed: [web-01]",
        "--- before: /etc/app/config.yml",
        "+++ after: /etc/app/config.yml",
        "-debug: true",
        "+debug: false",
        "changed: [web-03]",
    ]
    for line in lines:
        collector.feed(line)
    assert set(collector.by_host.keys()) == {"web-01", "web-03"}
