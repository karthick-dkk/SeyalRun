"""Shared test setup.

Puts the repo root on sys.path (so ``libs.*`` imports resolve) and provides a
``load_module`` fixture that imports a single source file by path. We load
service-internal modules (e.g. api-gateway's ``app.rbac``) by file path because
every service ships a package literally named ``app`` — importing them normally
would collide. The modules under test here are deliberately dependency-light
(stdlib + pyjwt + cryptography only) so the security-invariant suite runs
without a database, redis, or the FastAPI apps.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The suite was written with every approval step on. The product default is off
# (libs/adminapproval); tests for "off" set it themselves (monkeypatch.setenv).
# Forced rather than defaulted so a developer's own shell or .env cannot change
# what the suite asserts.
os.environ["SEYALRUN_ADMIN_APPROVAL"] = "on"


def _load(relpath: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    assert spec and spec.loader, f"cannot load {relpath}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def load_module():
    return _load
