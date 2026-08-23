"""What a service imports and what its requirements.txt pins must agree.

Found by taking the whole terminal down. The SFTP upload endpoint uses FastAPI's
Form/File/UploadFile parameters, which need `python-multipart` — and FastAPI
checks for it at ROUTE REGISTRATION, not on first request. So a missing pin was
not "uploads return 500"; it was `RuntimeError` during import, the service
crash-looping, and every SSH session on the box gone with it.

305 unit tests and a clean `npm run build` said nothing about it, because none of
them import the service. A missing dependency is invisible to every check that
does not actually load the module.

This maps the third-party imports of each service against its own
requirements.txt, and separately pins the framework features whose dependency is
implicit — the ones nothing in the source names, which are exactly the ones a
reader cannot spot.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ROOT / "services"

# import name -> distribution name, where they differ.
_DIST = {
    "jwt": "pyjwt", "yaml": "pyyaml", "dateutil": "python-dateutil",
    "redis": "redis", "sqlalchemy": "sqlalchemy", "multipart": "python-multipart",
    "argon2": "argon2-cffi",
}
# Provided by another pin, the stdlib, or the shared libs/ tree.
_IGNORE = {
    "app", "libs", "alembic", "starlette", "anyio", "typing_extensions",
    "zxcvbn", "asyncio", "__future__",
}

# FastAPI features whose dependency is implicit — nothing in the source imports
# the package, so only a rule like this can connect them.
_IMPLICIT = [
    (re.compile(r"\b(?:Form|File|UploadFile)\b"), "python-multipart",
     "FastAPI Form/File/UploadFile parameters; validated at route registration, "
     "so a missing pin crash-loops the service rather than failing one request"),
]


def _stdlib_names() -> set[str]:
    """sys.stdlib_module_names is 3.10+. This suite also runs on 3.9, where its
    absence silently yields an empty set and flags every stdlib import as a
    missing dependency — the first draft of this test did exactly that."""
    import sys, sysconfig, os
    names = set(getattr(sys, "stdlib_module_names", ()) or ())
    if names:
        return names
    names = set(sys.builtin_module_names)
    stdlib = sysconfig.get_paths().get("stdlib")
    if stdlib and os.path.isdir(stdlib):
        for entry in os.listdir(stdlib):
            if entry.endswith(".py"):
                names.add(entry[:-3])
            elif "." not in entry:
                names.add(entry)
    return names


_STDLIB = _stdlib_names()


def _norm(name: str) -> str:
    """PyPI treats _ and - as equivalent; requirements files pick one."""
    return name.replace("_", "-").lower()


def _service_dirs() -> list[Path]:
    return sorted(d for d in SERVICES.iterdir() if (d / "requirements.txt").exists())


def _pinned(service: Path) -> set[str]:
    out = set()
    for line in (service / "requirements.txt").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[=<>\[;]", line)[0].strip()
        if name:
            out.add(_norm(name))
    return out


def _top_level_imports(service: Path) -> set[str]:
    mods = set()
    for py in (service / "app").rglob("*.py"):
        try:
            tree = ast.parse(py.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods.add(node.module.split(".")[0])
    return mods


def test_services_are_discovered():
    """Guard against every parametrised case below being skipped silently."""
    dirs = _service_dirs()
    assert len(dirs) >= 5, f"only found {[d.name for d in dirs]}"
    assert "fastapi" in _pinned(SERVICES / "terminal-service")
    assert "json" in _STDLIB and "posixpath" in _STDLIB, "stdlib detection failed"


@pytest.mark.parametrize("service", _service_dirs(), ids=lambda p: p.name)
def test_third_party_imports_are_pinned(service: Path):
    pinned = _pinned(service)
    missing = sorted(
        _DIST.get(m, m) for m in _top_level_imports(service)
        if m not in _STDLIB and m not in _IGNORE and _norm(_DIST.get(m, m)) not in pinned
    )
    assert not missing, (
        f"{service.name} imports these but does not pin them — the container will "
        f"fail at import, not at first use: {missing}"
    )


@pytest.mark.parametrize("service", _service_dirs(), ids=lambda p: p.name)
def test_implicit_framework_dependencies_are_pinned(service: Path):
    pinned = _pinned(service)
    problems = []
    for pattern, dist, why in _IMPLICIT:
        for py in (service / "app").rglob("*.py"):
            src = py.read_text()
            # Only where it is used as a FastAPI parameter, not in a comment.
            if not re.search(r"(?:=|:)\s*" + pattern.pattern, src) and \
               not re.search(r":\s*UploadFile", src):
                continue
            if _norm(dist) not in pinned:
                problems.append(f"{py.relative_to(SERVICES)} needs {dist} — {why}")
            break
    assert not problems, "\n  ".join(problems)
