"""Every audit record must carry a success/failure indication.

PCI DSS Req 10.2.2 lists it as a required element of an audit record. It was
missing from 48 of 64 `log_action` call sites: failures recorded
`result="failure"` while the successes beside them recorded nothing, so the
outcome could not be filtered or reported on uniformly. Found by re-baselining
staging and *reading* the fresh chain rather than only verifying it — the first
three rows were two `login_failed`/failure and one `login`/null.

The obvious fix is a default of "success" inside log_action. That is a trap and
this test exists partly to keep anyone from taking it: a failure path that
forgot to pass "failure" would then positively assert success, which is worse
than a null. The indication has to be written at the call site, deliberately,
which is what this checks.

Regression: R-10 — found by /qa on 2026-08-11
Report: docs/compliance/risk-register.md
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ROOT / "services"


def _call_sites() -> list[tuple[str, int, str, bool]]:
    """(file, line, action, has_result) for every log_action *call*.

    Skips `def log_action(...)` itself — the definitions take result as a
    parameter and are not call sites.
    """
    out = []
    for f in sorted(SERVICES.rglob("*.py")):
        src = f.read_text()
        for m in re.finditer(r"log_action\(", src):
            if re.search(r"def\s+$", src[: m.start()]):
                continue
            i, depth = m.end(), 1
            while depth:
                depth += (src[i] == "(") - (src[i] == ")")
                i += 1
            call = src[m.end() : i - 1]
            action = re.search(r'action=("([^"]+)")', call)
            out.append((
                str(f.relative_to(ROOT)),
                src[: m.start()].count("\n") + 1,
                action.group(2) if action else "<computed>",
                "result=" in call,
            ))
    return out


def test_call_sites_are_found():
    """Guard against the scan silently returning nothing, which would make the
    check below vacuously pass — the failure mode that hid R-9."""
    sites = _call_sites()
    assert len(sites) > 30, f"only found {len(sites)} call sites, parser is probably broken"


@pytest.mark.parametrize(
    "site", [s for s in _call_sites() if not s[3]], ids=lambda s: f"{s[0]}:{s[1]}"
)
def test_every_audit_call_records_an_outcome(site):
    path, line, action, _ = site
    pytest.fail(
        f"{path}:{line} logs {action!r} without result=. Every audit record needs a "
        "success/failure indication (PCI DSS Req 10.2.2). Decide which it is at the "
        "call site — do NOT add a default to log_action, or a failure path that "
        "forgets to pass 'failure' will positively assert success."
    )


def test_denials_are_recorded_as_failures():
    """A denial is not a success. login_denied_ip is followed immediately by a
    403, so it must not be filed alongside the logins that worked."""
    src = (SERVICES / "identity-service/app/api/auth.py").read_text()
    for m in re.finditer(r'action="login_denied_ip"', src):
        i, depth = src.rindex("log_action(", 0, m.start()) + len("log_action("), 1
        while depth:
            depth += (src[i] == "(") - (src[i] == ")")
            i += 1
        assert 'result="failure"' in src[:i], "login_denied_ip must record a failure"
