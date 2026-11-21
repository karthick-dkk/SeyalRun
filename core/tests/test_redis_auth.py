"""Redis requires a password (F-4), and every deploy path gives an .env one.

Sessions, JIT-elevation flags and login-lockout counters live in Redis, so an
unauthenticated Redis let any container on the bridge network read or forge
them. The fix has two halves that must both hold:
  * compose starts Redis with --requirepass and refuses to start without it;
  * every path that produces or reuses an .env (install.sh, which runs standalone
    via curl | bash, and ops/ensure-redis-password.sh for the other scripts)
    generates the password and points the bundled-Redis URL at it.

The scripts are executed, not grepped: what matters is the .env they leave.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent.parent
ENSURE = REPO / "ops/ensure-redis-password.sh"
COMPOSE_FILES = ["docker-compose.yml", "docker-compose.prod.yml"]

pytestmark = pytest.mark.skipif(not shutil.which("bash") or not shutil.which("openssl"),
                                reason="needs bash + openssl")


def _env(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v
    return out


def _install_snippet() -> str:
    """The inline Redis step from install.sh, runnable on its own."""
    src = (REPO / "install.sh").read_text()
    start = src.index("# Redis auth (F-4).")
    end = src.index("esac", start) + len("esac")
    return 'ok(){ :; }; warn(){ echo "$*" >&2; }\n' + src[start:end] + "\n"


def _run_ensure(tmp: Path, how: str) -> subprocess.CompletedProcess:
    if how == "script":
        return subprocess.run(["bash", str(ENSURE), str(tmp / ".env")], capture_output=True, text=True, check=True)
    return subprocess.run(["bash", "-c", "set -euo pipefail\n" + _install_snippet()],
                          cwd=tmp, capture_output=True, text=True, check=True)


@pytest.fixture(params=["script", "install.sh"])
def how(request):
    return request.param


def test_fresh_env_from_example_gets_password_and_authenticated_url(tmp_path, how):
    shutil.copy(REPO / ".env.example", tmp_path / ".env")
    _run_ensure(tmp_path, how)
    env = _env(tmp_path / ".env")
    pw = env["REDIS_PASSWORD"]
    assert re.fullmatch(r"[0-9a-f]{64}", pw)
    assert env["REDIS_URL"] == f"redis://:{pw}@redis:6379/0"


def test_upgraded_env_without_password_is_fixed(tmp_path, how):
    (tmp_path / ".env").write_text("JWT_SECRET=x\nREDIS_URL=redis://redis:6379/0   # old comment\n")
    _run_ensure(tmp_path, how)
    env = _env(tmp_path / ".env")
    assert env["REDIS_URL"] == f"redis://:{env['REDIS_PASSWORD']}@redis:6379/0"
    assert env["JWT_SECRET"] == "x"


def test_is_idempotent(tmp_path, how):
    shutil.copy(REPO / ".env.example", tmp_path / ".env")
    _run_ensure(tmp_path, how)
    first = (tmp_path / ".env").read_text()
    _run_ensure(tmp_path, how)
    assert (tmp_path / ".env").read_text() == first, "a re-run must not rotate the password"


def test_custom_redis_url_is_left_alone(tmp_path, how):
    custom = "rediss://:theirs@redis.example.internal:6380/2"
    (tmp_path / ".env").write_text(f"REDIS_URL={custom}\n")
    _run_ensure(tmp_path, how)
    env = _env(tmp_path / ".env")
    assert env["REDIS_URL"] == custom
    assert env["REDIS_PASSWORD"]  # still generated, harmless for an external Redis


def test_every_deploy_path_runs_the_redis_step():
    for script in ["ops/_staging-bootstrap.sh", "ops/deploy-staging-images.sh"]:
        assert "ops/ensure-redis-password.sh" in (REPO / script).read_text(), script
    assert "# Redis auth (F-4)." in (REPO / "install.sh").read_text()


@pytest.mark.parametrize("compose", COMPOSE_FILES)
def test_compose_redis_requires_the_password_and_fails_closed(compose):
    redis = yaml.safe_load((REPO / compose).read_text())["services"]["redis"]
    cmd = redis["command"]
    i = cmd.index("--requirepass")
    assert cmd[i + 1].startswith("${REDIS_PASSWORD:?"), "an unset password must stop compose, not start an open Redis"
    assert redis["environment"]["REDISCLI_AUTH"].startswith("${REDIS_PASSWORD"), "healthcheck must authenticate"
