"""Rollout decisions (3.0 M4): pure logic, no database."""

from __future__ import annotations

import base64
import gzip
import importlib
import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SVC = Path(__file__).resolve().parent.parent / "services/posture-service/app"


def _load():
    if "posture_core_pkg" not in sys.modules:
        import types

        pkg = types.ModuleType("posture_core_pkg")
        pkg.__path__ = [str(SVC)]
        sys.modules["posture_core_pkg"] = pkg
    return importlib.import_module("posture_core_pkg.rollout_core")


core = _load()


def cfg(**kw):
    return core.normalise_config(kw)


def hosts(n, clusters=None):
    return [{"host_id": f"h{i:03}", "family": ("debian", "rhel")[i % 2], "clusters": (clusters or {}).get(i, [])} for i in range(n)]


def test_wave_sizes_for_300_hosts():
    waves = core.plan_waves(hosts(300), cfg())
    assert [len(w) for w in waves] == [3, 30, 50, 50, 50, 50, 50, 17]
    assert {int(h[1:]) % 2 for h in waves[0]} == {0, 1}, "the canary covers both families"
    flat = [h for w in waves for h in w]
    assert len(flat) == len(set(flat)) == 300


def test_one_cluster_member_per_wave():
    waves = core.plan_waves(hosts(20, {i: ["c1"] for i in range(5)}), cfg(batch_size=500))
    members = {f"h{i:03}" for i in range(5)}
    assert all(len(members & set(w)) <= 1 for w in waves) and sum(len(w) for w in waves) == 20


@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_small_fleets_plan(n):
    assert sum(len(w) for w in core.plan_waves(hosts(n), cfg())) == n


@pytest.mark.parametrize("bad", [{"max_failuers": 0}, {"soak_minutes": -1}, {"soak_minutes": True}, {"scope": "kev"}, {"exclude": ["a;b"]},
                                 {"health_urls": ["file:///etc/passwd"]}, {"health_urls": ["http://x/$(id)"]}, {"scope": "packages"},
                                 {"drain_template_id": "x"}, {"reboot_wait_seconds": 5}, {"zabbix": "maybe"}])
def test_bad_settings_are_refused(bad):
    with pytest.raises(core.PlanError):
        core.normalise_config(bad)


def win(**kw):
    return {"name": "w", "timezone": "UTC", "days": [5], "start": "22:00", "duration_minutes": 240, **kw}


def test_window_crosses_midnight():
    w = win()       # Saturday 22:00 to Sunday 02:00
    sat = datetime(2026, 11, 28, 22, 0, tzinfo=UTC)
    assert core.window_end(w, sat - timedelta(minutes=1)) is None
    assert core.window_end(w, sat) == sat + timedelta(hours=4)
    assert core.window_end(w, sat + timedelta(hours=3, minutes=59)) is not None
    assert core.window_end(w, sat + timedelta(hours=4)) is None
    assert core.next_open(w, sat) == sat + timedelta(days=7)


def test_window_follows_the_local_clock_across_dst():
    w = win(timezone="Europe/Berlin", days=list(range(7)), start="02:30", duration_minutes=60)
    # 2027-03-28: 02:30 does not exist (02:00 -> 03:00). The window still opens once.
    starts = [core._occurrence(w, datetime(2027, 3, d).date())[0] for d in (27, 28, 29)]
    assert starts[0] == datetime(2027, 3, 27, 1, 30, tzinfo=UTC)
    assert starts[2] == datetime(2027, 3, 29, 0, 30, tzinfo=UTC)
    assert starts[0] < starts[1] < starts[2]


@pytest.mark.parametrize("bad", [{"timezone": "Mars/Base"}, {"timezone": "../etc"}, {"start": "24:00"}, {"days": []}, {"days": [7]},
                                 {"duration_minutes": 5}, {"duration_minutes": 2000}])
def test_bad_windows_are_refused(bad):
    with pytest.raises(core.PlanError):
        core.check_window(win(**bad))


PENDING = [{"name": "libssl3", "arch": "amd64", "current": "1", "available": "2", "security": True},
           {"name": "vim", "arch": "amd64", "current": "1", "available": "2", "security": False}]
INST = [{"name": "libssl3", "arch": "amd64", "source": "openssl"}, {"name": "vim", "arch": "amd64", "source": "vim"}]


def names(**kw):
    found = kw.pop("found", [])
    return [p["name"] for p in core.select_packages(PENDING, INST, found, cfg(**kw))]


def test_package_selection():
    assert names() == ["libssl3"]
    assert names(scope="all") == ["libssl3", "vim"]
    assert names(scope="all", exclude=["lib*"]) == ["vim"]
    assert names(scope="findings", found=[{"package": "openssl"}]) == ["libssl3"], "advisories name the source package"
    assert names(scope="packages", include=["vim", "not-installed"]) == ["vim"], "a plan never installs something new"
    assert names(include=["vim"], exclude=["vim"]) == ["libssl3"], "exclude wins"


def snap(**kw):
    return {"marker": "b1", "kernel": "k", "init": "systemd", "state": "", "services": ["a.service"], "failed": [], "ports": ["*:22", "*:80"],
            "mounts": ["/"], "free_kb": {"/": 9_000_000}, "sessions": 0, "health": {}, "problems": [], **kw}


def bad(checks):
    return {c["name"] for c in core.failed(checks)}


def test_post_checks():
    ok = snap(marker="b2")
    assert bad(core.postcheck(snap(), ok, cfg(), rebooted=True)) == set()
    assert bad(core.postcheck(snap(), snap(), cfg(), rebooted=True)) == {"rebooted"}
    assert bad(core.postcheck(snap(), snap(services=[]), cfg(), rebooted=False)) == {"services running"}
    assert bad(core.postcheck(snap(), snap(services=[]), cfg(ignore_services=["a"]), rebooted=False)) == set()
    assert bad(core.postcheck(snap(), snap(failed=["x.service"]), cfg(), rebooted=False)) == {"no newly failed services"}
    assert bad(core.postcheck(snap(failed=["x.service"]), snap(failed=["x.service"]), cfg(), rebooted=False)) == set()
    assert bad(core.postcheck(snap(), snap(ports=["*:22"]), cfg(), rebooted=False)) == {"ports listening"}
    assert bad(core.postcheck(snap(ports=["*:22", "*:80", "*:45001"]), snap(), cfg(), rebooted=False)) == set(), "ephemeral port"
    assert bad(core.postcheck(snap(mounts=["/", "/data"]), snap(), cfg(), rebooted=False)) == {"mounts present"}
    assert bad(core.postcheck(snap(), snap(problems=["half-configured"]), cfg(), rebooted=False)) == {"package database"}
    u = "http://127.0.0.1/h"
    assert bad(core.postcheck(snap(), snap(health={u: "500"}), cfg(health_urls=[u]), rebooted=False)) == {f"health {u}"}
    assert bad(core.postcheck(snap(), snap(), cfg(health_urls=[u]), rebooted=False)) == {f"health {u}"}, "no answer is not healthy"


def test_pre_checks():
    assert bad(core.precheck(snap(), cfg())) == set()
    assert bad(core.precheck(snap(free_kb={"/": 1000}), cfg())) == {"disk /"}
    assert bad(core.precheck(snap(free_kb={}), cfg())) == {"disk /"}, "unknown free space is not enough space"
    assert bad(core.precheck(snap(problems=["x"]), cfg())) == {"package database"}
    assert bad(core.precheck(snap(sessions=1), cfg(block_on_sessions=True))) == {"no interactive sessions"}


def verify(installed, target="2.0", scheme="deb", held=()):
    plan = [{"name": "p", "arch": "amd64", "current": "1", "target": target, "security": True}]
    return core.verify_packages(plan, [{"name": "p", "arch": "amd64", "version": v} for v in installed], scheme, held)[0]["ok"]


def test_verification_compares_versions_properly():
    assert verify(["2.0"]) and verify(["10.0"], "9.0") and verify(["1.0", "2.0"]), "newest side-by-side kernel counts"
    assert verify(["1.9"]) is False and verify([]) is False and verify(["2.0~rc1"]) is False
    assert verify(["1.0"], held=["p"]) is None
    assert verify(["1:1.0"], "2.0") is True, "epoch"


def test_reboot_decision():
    kern, plain = [{"name": "kernel-core", "new": "2"}], [{"name": "vim", "new": "2"}]
    assert core.reboot_needed("never", True, kern)[0] is False
    assert core.reboot_needed("always", False, [])[0] is True
    assert core.reboot_needed("if_required", True, plain)[0] is True
    assert core.reboot_needed("if_required", False, kern)[0] is False
    assert core.reboot_needed("if_required", None, kern)[0] is True
    assert core.reboot_needed("if_required", None, plain)[0] is False


def pay(recs, kind="PKG", enc="gz"):
    raw = "\n".join(recs).encode()
    b = base64.b64encode(gzip.compress(raw) if enc == "gz" else raw).decode()
    return ["[host:x] noise", f"[host:x] SEYALRUN-{kind}-BEGIN v1 {enc}", *(f"[host:x] {b[i:i + 40]}" for i in range(0, len(b), 40)),
            f"[host:x] SEYALRUN-{kind}-END"]


@pytest.mark.parametrize("enc", ["gz", "raw"])
def test_change_payload(enc):
    ch = core.parse_changes(pay(["V\t1", "M\tapt", "H\tvim", "-\ta\tamd64\t1", "+\ta\tamd64\t2", "+\tb\tamd64\t1", "Z\tend"], enc=enc))
    assert core.pair_changes(ch) == [{"name": "a", "arch": "amd64", "old": "1", "new": "2"}, {"name": "b", "arch": "amd64", "old": None, "new": "1"}]
    assert ch["held"] == ["vim"]


def test_broken_payloads_are_errors():
    good = pay(["M\tapt", "Z\tend"])
    for lines in ([], good[:-1], pay(["M\tapt"]), [good[1], "!!!", good[-1]]):
        with pytest.raises(core.PayloadError):
            core.parse_changes(lines)
    assert core.parse_changes(pay(["M\tdnf", "Z\tend"]) + good)["manager"] == "apt", "the last payload counts"
    with pytest.raises(core.PayloadError):
        core.parse_snapshot(pay(["K\tk", "Z\tend"], "SNAP"))


def test_payload_size_is_bounded():
    with pytest.raises(core.PayloadError):
        core._payload(pay(["x" * 5000, "Z\tend"]), "PKG", limit=1000)


def test_plan_hash_covers_everything():
    p = {"hosts": [{"host_id": "h", "packages": []}], "config": cfg()}
    h = core.plan_hash(p)
    assert h == core.plan_hash({"config": cfg(), "hosts": p["hosts"]})
    assert h != core.plan_hash({**p, "config": cfg(reboot="never")})


def test_suse_facts_reach_the_planner():
    sys.path.insert(0, str(SVC.parent))
    facts = importlib.import_module("posture_core_pkg.facts")
    text = "\n".join(["V\t1", "M\trpm", 'R\tID="opensuse-leap"', 'R\tID_LIKE="suse opensuse"', "I\tcurl\t8.0.1-1\taarch64\tinstalled",
                      "W\tv  | Update repository | curl | 8.0.1-1 | 8.0.1-2 | aarch64", "Z\tend"])
    f = facts.parse_facts(text)
    assert f["distro_family"] == "suse" and f["pending"][0]["available"] == "8.0.1-2" and f["pending"][0]["arch"] == "aarch64"
