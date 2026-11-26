"""Patch rollout decisions (3.0 M4): pure functions, no database and no network.

Windows, wave planning, package selection, pre/post checks and verification. The
engine (rollout_engine.py) only moves state; every yes/no it acts on is decided here.
"""

from __future__ import annotations

import base64
import binascii
import fnmatch
import gzip
import hashlib
import io
import json
import re
import zlib
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import vercmp

SCOPES = ("security", "all", "findings", "packages")
REBOOT_POLICIES = ("never", "if_required", "always")
ZABBIX_MODES = ("off", "optional", "required")
# exact: install the approved version or fail. at_least: whatever the mirror has by then.
VERSION_MODES = ("exact", "at_least")

DEFAULTS: dict = {
    "scope": "security", "versions": "exact", "include": [], "exclude": [], "reboot": "if_required",
    "canary_size": 3, "second_percent": 10, "batch_size": 50, "concurrency": 25,
    "max_failures": 0, "soak_minutes": 10, "reboot_wait_seconds": 900,
    "min_free_mb_root": 1024, "min_free_mb_boot": 100, "block_on_sessions": False,
    "health_urls": [], "ignore_services": [], "cluster_group_ids": [],
    "zabbix": "optional", "zabbix_min_severity": 3, "rollback_on_failure": False,
    "drain_template_id": None, "undrain_template_id": None,
}
_INT_RANGES = {
    "canary_size": (1, 100), "second_percent": (1, 100), "batch_size": (1, 500), "concurrency": (1, 100),
    "max_failures": (0, 1000), "soak_minutes": (0, 1440), "reboot_wait_seconds": (60, 3000),
    "min_free_mb_root": (0, 1_000_000), "min_free_mb_boot": (0, 1_000_000), "zabbix_min_severity": (0, 5),
}
_GLOB = re.compile(r"[A-Za-z0-9*?][A-Za-z0-9._+*?-]{0,199}\Z")
_UNIT = re.compile(r"[A-Za-z0-9@*?][A-Za-z0-9:_.@*?\\-]{0,199}\Z")
_HEALTH = re.compile(r"https?://[A-Za-z0-9.-]+(?::\d{1,5})?(?:/[A-Za-z0-9._~/%+?&=-]*)?\Z")
_ID = re.compile(r"[A-Za-z0-9-]{1,36}\Z")


class PlanError(ValueError):
    """The request cannot become a safe plan; the message is shown to the user."""


def normalise_config(raw: dict) -> dict:
    """Defaults + validation. Unknown keys are refused: a misspelt safety setting
    that is silently ignored is worse than an error."""
    unknown = set(raw) - set(DEFAULTS)
    if unknown:
        raise PlanError("unknown setting(s): " + ", ".join(sorted(unknown)))
    cfg = {**DEFAULTS, **raw}
    for key, (lo, hi) in _INT_RANGES.items():
        v = cfg[key]
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise PlanError(f"{key} must be a whole number from {lo} to {hi}")
    for key, allowed in (("scope", SCOPES), ("reboot", REBOOT_POLICIES), ("zabbix", ZABBIX_MODES),
                         ("versions", VERSION_MODES)):
        if cfg[key] not in allowed:
            raise PlanError(f"{key} must be one of {', '.join(allowed)}")
    for key in ("block_on_sessions", "rollback_on_failure"):
        if not isinstance(cfg[key], bool):
            raise PlanError(f"{key} must be true or false")
    for key, rx, limit in (("include", _GLOB, 500), ("exclude", _GLOB, 500), ("ignore_services", _UNIT, 100),
                           ("health_urls", _HEALTH, 10), ("cluster_group_ids", _ID, 100)):
        v = cfg[key]
        if not isinstance(v, list) or len(v) > limit or not all(isinstance(x, str) and rx.match(x) for x in v):
            raise PlanError(f"{key} has an invalid entry")
        cfg[key] = list(dict.fromkeys(v))
    for key in ("drain_template_id", "undrain_template_id"):
        if cfg[key] is not None and not (isinstance(cfg[key], str) and _ID.match(cfg[key])):
            raise PlanError(f"{key} is invalid")
    if bool(cfg["drain_template_id"]) != bool(cfg["undrain_template_id"]):
        raise PlanError("set both the drain and the undrain template, or neither")
    if cfg["scope"] == "packages" and not cfg["include"]:
        raise PlanError("scope 'packages' needs at least one package in include")
    return cfg


# ── maintenance windows ──────────────────────────────────────────────────────

_HHMM = re.compile(r"([01]\d|2[0-3]):([0-5]\d)\Z")


def check_window(w: dict) -> None:
    try:
        ZoneInfo(w["timezone"])
    except (ZoneInfoNotFoundError, ValueError, OSError, TypeError, KeyError) as exc:
        raise PlanError("unknown timezone") from exc
    if not isinstance(w.get("start"), str) or not _HHMM.match(w["start"]):
        raise PlanError("start must be HH:MM (00:00 to 23:59)")
    days = w.get("days")
    if not isinstance(days, list) or not days or not all(type(d) is int and 0 <= d <= 6 for d in days):
        raise PlanError("pick at least one day (0 = Monday … 6 = Sunday)")
    dur = w.get("duration_minutes")
    if type(dur) is not int or not 15 <= dur <= 1440:
        raise PlanError("duration must be 15 to 1440 minutes")


def _occurrence(w: dict, day) -> tuple[datetime, datetime]:
    h, m = (int(x) for x in _HHMM.match(w["start"]).groups())
    # A wall time that does not exist (DST gap) resolves to the moment after the gap.
    local = datetime(day.year, day.month, day.day, h, m, tzinfo=ZoneInfo(w["timezone"]))
    start = local.astimezone(UTC)
    return start, start + timedelta(minutes=w["duration_minutes"])


def window_end(w: dict, now: datetime) -> datetime | None:
    """End of the occurrence ``now`` falls in, or None when the window is closed."""
    today = now.astimezone(ZoneInfo(w["timezone"])).date()
    for back in (0, 1):         # an occurrence can start yesterday and cross midnight
        day = today - timedelta(days=back)
        if day.weekday() in w["days"]:
            start, end = _occurrence(w, day)
            if start <= now < end:
                return end
    return None


def next_open(w: dict, now: datetime) -> datetime | None:
    today = now.astimezone(ZoneInfo(w["timezone"])).date()
    for ahead in range(0, 9):
        day = today + timedelta(days=ahead)
        if day.weekday() in w["days"]:
            start, _ = _occurrence(w, day)
            if start > now:
                return start
    return None


# ── package selection ────────────────────────────────────────────────────────

def _match(name: str, globs: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, g) for g in globs)


def select_packages(pending: list[dict], installed: list[dict], findings: list[dict], cfg: dict) -> list[dict]:
    """Exactly what will be upgraded on one host. Only packages with a pending update
    are ever selected, so a plan never installs something new by name."""
    affected = {f["package"] for f in findings}
    source_of = {(p["name"], p.get("arch") or ""): p.get("source") or p["name"] for p in installed}
    out: dict[tuple[str, str], dict] = {}
    for p in pending:
        name, arch = p["name"], p.get("arch") or ""
        if _match(name, cfg["exclude"]):
            continue
        scope = cfg["scope"]
        if scope == "security":
            chosen = bool(p.get("security"))
        elif scope == "findings":
            # Advisories on Debian name the SOURCE package; hosts install binaries.
            src = source_of.get((name, arch)) or next((s for (n, _), s in source_of.items() if n == name), name)
            chosen = name in affected or src in affected
        elif scope == "packages":
            chosen = _match(name, cfg["include"])
        else:
            chosen = True
        if scope in ("security", "findings") and cfg["include"] and _match(name, cfg["include"]):
            chosen = True
        if chosen:
            out[(name, arch)] = {"name": name, "arch": arch, "current": p.get("current") or "",
                                 "target": p.get("available") or "", "security": bool(p.get("security"))}
    return sorted(out.values(), key=lambda x: (x["name"], x["arch"]))


# ── waves ────────────────────────────────────────────────────────────────────

def plan_waves(hosts: list[dict], cfg: dict) -> list[list[str]]:
    """hosts: [{host_id, family, clusters: [group ids]}]. Canary, then second_percent,
    then batches. Two members of one cluster are never in the same wave."""
    ordered = sorted(hosts, key=lambda h: h["host_id"])
    left = {h["host_id"]: h for h in ordered}
    waves: list[list[str]] = []

    def fill(size: int, *, cover_families: bool = False) -> None:
        picked: list[str] = []
        used: set[str] = set()

        def take(h: dict) -> bool:
            if len(picked) >= size or used & set(h["clusters"]):
                return False
            picked.append(h["host_id"])
            used.update(h["clusters"])
            left.pop(h["host_id"])
            return True

        if cover_families:
            for fam in sorted({h["family"] for h in ordered}):
                for h in [x for x in ordered if x["family"] == fam and x["host_id"] in left]:
                    if take(h):
                        break
        for h in [x for x in ordered if x["host_id"] in left]:
            if len(picked) >= size:
                break
            take(h)
        waves.append(picked)

    if not ordered:
        return []
    fill(max(cfg["canary_size"], 1), cover_families=True)
    if left:
        fill(max(1, -(-len(ordered) * cfg["second_percent"] // 100)))
    while left:
        fill(cfg["batch_size"])
    return [w for w in waves if w]


def plan_hash(plan: dict) -> str:
    """Approval binds to this: what was approved is byte for byte what runs."""
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# ── run output payloads ──────────────────────────────────────────────────────

_PREFIX = re.compile(r"^\[host:[^\]]*\] ?")


class PayloadError(ValueError):
    pass


def _payload(lines: list[str], kind: str, limit: int = 32 * 1024 * 1024) -> list[list[str]]:
    """Records of the LAST complete payload of ``kind`` in a run's output."""
    clean = [_PREFIX.sub("", str(ln)) for ln in lines]
    try:
        end = len(clean) - 1 - clean[::-1].index(f"SEYALRUN-{kind}-END")
        begin = next(i for i in range(end, -1, -1) if clean[i].startswith(f"SEYALRUN-{kind}-BEGIN v1"))
    except (ValueError, StopIteration) as exc:
        raise PayloadError("the run produced no result payload") from exc
    try:
        raw = base64.b64decode("".join(clean[begin + 1:end]), validate=True)
        if clean[begin].endswith(" gz"):
            dec = gzip.GzipFile(fileobj=io.BytesIO(raw))
            raw = dec.read(limit + 1)
        if len(raw) > limit:
            raise PayloadError("result payload is too large")
    except (binascii.Error, zlib.error, OSError, EOFError, ValueError) as exc:
        raise PayloadError("the result payload is damaged") from exc
    recs = [ln.split("\t") for ln in raw.decode("utf-8", "replace").splitlines() if ln]
    if not recs or recs[-1] != ["Z", "end"]:
        raise PayloadError("the result payload is incomplete")
    return recs


def parse_changes(lines: list[str]) -> dict:
    out: dict = {"manager": "", "held": [], "removed": [], "added": [], "plan": []}
    for r in _payload(lines, "PKG"):
        if r[0] in "MH" and len(r) < 2:
            raise PayloadError("the result payload is damaged")
        if r[0] == "M":
            out["manager"] = r[1]
        elif r[0] == "H":
            out["held"].append(r[1])
        elif r[0] in "+-" and len(r) >= 4:
            out["added" if r[0] == "+" else "removed"].append({"name": r[1], "arch": r[2], "version": r[3]})
        elif r[0] == "P":
            out["plan"].append("\t".join(r[1:]))
    return out


def pair_changes(ch: dict) -> list[dict]:
    """[{name, arch, old, new}] — old None: newly installed, new None: removed."""
    olds: dict[tuple, list[str]] = {}
    for x in ch["removed"]:
        olds.setdefault((x["name"], x["arch"]), []).append(x["version"])
    out = []
    for x in ch["added"]:
        old = olds.get((x["name"], x["arch"])) or []
        out.append({"name": x["name"], "arch": x["arch"], "old": old.pop(0) if old else None, "new": x["version"]})
    for (name, arch), rest in olds.items():
        out += [{"name": name, "arch": arch, "old": v, "new": None} for v in rest]
    return sorted(out, key=lambda c: (c["name"], c["arch"]))


def merge_changes(first: list[dict], then: list[dict]) -> list[dict]:
    """What two runs changed together: the oldest "old" and the newest "new"."""
    out = {(c["name"], c["arch"], c["old"] if c["old"] is None else ""): dict(c) for c in first}
    for c in then:
        key = next((k for k, v in out.items() if k[:2] == (c["name"], c["arch"]) and c["old"] is not None
                    and v["new"] == c["old"]), None)
        if key:
            out[key]["new"] = c["new"]
        else:
            out[(c["name"], c["arch"], c["old"] or f"+{c['new']}")] = dict(c)
    return sorted((c for c in out.values() if c["old"] != c["new"]), key=lambda c: (c["name"], c["arch"]))


def parse_snapshot(lines: list[str]) -> dict:
    snap: dict = {"marker": "", "kernel": "", "init": "", "state": "", "services": [], "failed": [], "ports": [],
                  "mounts": [], "free_kb": {}, "sessions": 0, "health": {}, "problems": []}
    for r in _payload(lines, "SNAP"):
        t, v = r[0], (r[1] if len(r) > 1 else "")
        if t == "B":
            snap["marker"] = v
        elif t == "K":
            snap["kernel"] = v
        elif t == "I":
            snap["init"] = v
        elif t == "Y":
            snap["state"] = v
        elif t == "S":
            snap["services"].append(v)
        elif t == "F":
            snap["failed"].append(v)
        elif t == "L":
            snap["ports"].append(v)
        elif t == "D":
            snap["mounts"].append(v)
        elif t == "A" and len(r) > 2 and r[2].isdigit():
            snap["free_kb"][v] = int(r[2])
        elif t == "U" and v.isdigit():
            snap["sessions"] = int(v)
        elif t == "X":
            snap["problems"].append(v)
        elif t == "H" and len(r) > 2:
            snap["health"][v] = r[2]
    if not snap["marker"]:
        raise PayloadError("the snapshot has no boot marker")
    return snap


# ── checks ───────────────────────────────────────────────────────────────────

def check(name: str, ok: bool | None, detail: str = "") -> dict:
    """ok None = not applicable / could not be evaluated, recorded as such."""
    return {"name": name, "ok": ok, "detail": detail[:500]}


def failed(checks: list[dict]) -> list[dict]:
    return [c for c in checks if c["ok"] is False]


def precheck(snap: dict, cfg: dict) -> list[dict]:
    out = []
    for mount, key in (("/", "min_free_mb_root"), ("/boot", "min_free_mb_boot"), ("/var", "min_free_mb_root")):
        free = snap["free_kb"].get(mount)
        if free is None:
            out.append(check(f"disk {mount}", False if mount == "/" else None, "free space not reported"))
        else:
            out.append(check(f"disk {mount}", free // 1024 >= cfg[key], f"{free // 1024} MB free, {cfg[key]} MB needed"))
    out.append(check("package database", not snap["problems"], "; ".join(snap["problems"]) or "consistent"))
    if cfg["block_on_sessions"]:
        out.append(check("no interactive sessions", snap["sessions"] == 0, f"{snap['sessions']} logged in"))
    for url in cfg["health_urls"]:
        code = snap["health"].get(url, "000")
        out.append(check(f"health {url}", code == "200", f"HTTP {code}"))
    return out


# Not services that are expected to survive a reboot unchanged.
_TRANSIENT = ("session-*", "user@*", "user-runtime-dir@*", "run-*", "seyalrun-op-*", "systemd-*", "getty@*",
              "serial-getty@*", "packagekit*", "polkit*", "apt-daily*", "dnf-*", "fwupd*", "man-db*", "unattended-upgrades*")


def _ports(snap: dict) -> set[str]:
    """Ports worth comparing: the port number on a non-ephemeral range."""
    out = set()
    for p in snap["ports"]:
        num = p.rsplit(":", 1)[-1]
        if num.isdigit() and 0 < int(num) < 32768:
            out.add(num)
    return out


def postcheck(before: dict, after: dict, cfg: dict, *, rebooted: bool) -> list[dict]:
    ignore = [*_TRANSIENT, *cfg["ignore_services"]]
    ignore += [i + ".service" for i in cfg["ignore_services"] if not i.endswith(".service")]
    out = []
    if rebooted:
        out.append(check("rebooted", after["marker"] != before["marker"], "new boot" if after["marker"] != before["marker"] else "still on the old boot"))
    if before["init"] == "systemd":
        gone = sorted(s for s in set(before["services"]) - set(after["services"]) if not _match(s, ignore))
        out.append(check("services running", not gone, "missing: " + ", ".join(gone[:10]) if gone else f"{len(after['services'])} running"))
        new_failed = sorted(s for s in set(after["failed"]) - set(before["failed"]) if not _match(s, ignore))
        out.append(check("no newly failed services", not new_failed, ", ".join(new_failed[:10])))
    else:
        out.append(check("services running", None, "the host does not run systemd"))
    lost = sorted(_ports(before) - _ports(after), key=int)
    out.append(check("ports listening", not lost, "no longer listening: " + ", ".join(lost[:15]) if lost else f"{len(_ports(after))} ports"))
    gone = sorted(set(before["mounts"]) - set(after["mounts"]))
    out.append(check("mounts present", not gone, "missing: " + ", ".join(gone[:10]) if gone else f"{len(after['mounts'])} mounts"))
    out.append(check("package database", not after["problems"], "; ".join(after["problems"]) or "consistent"))
    for url in cfg["health_urls"]:
        code = after["health"].get(url, "000")
        out.append(check(f"health {url}", code == "200", f"HTTP {code}"))
    return out


_REBOOT_PKG = re.compile(r"(kernel(-core|-default|-rt.*|-modules.*)?|linux-image-.*|glibc|libc6|systemd|dbus(-broker)?|"
                         r"linux-firmware|openssl-libs|libssl\d.*|libopenssl\d.*)\Z")


def reboot_needed(policy: str, flag: bool | None, changes: list[dict]) -> tuple[bool, str]:
    if policy == "never":
        return False, "policy: never"
    if policy == "always":
        return True, "policy: always"
    if flag is not None:
        return flag, "the host reports a reboot is required" if flag else "the host reports no reboot is required"
    hit = sorted({c["name"] for c in changes if c["new"] and _REBOOT_PKG.match(c["name"])})
    if hit:
        return True, "the host cannot tell; changed: " + ", ".join(hit[:5])
    return False, "the host cannot tell; no kernel, libc, systemd or OpenSSL change"


def verify_packages(plan: list[dict], installed: list[dict], scheme: str, held: Iterable[str]) -> list[dict]:
    """One check per planned package: installed at or above the planned version?"""
    have: dict[str, list[tuple[str, str]]] = {}
    for p in installed:
        have.setdefault(p["name"], []).append((p.get("arch") or "", p["version"]))
    held, out = set(held), []
    for p in plan:
        name = p["name"]
        if name in held:
            out.append(check(f"package {name}", None, "held on the host, left alone"))
            continue
        versions = [v for a, v in have.get(name, []) if not p["arch"] or a in (p["arch"], "noarch", "all")] \
            or [v for _, v in have.get(name, [])]
        if not versions:
            out.append(check(f"package {name}", False, "not installed after the patch"))
            continue
        # Kernels install side by side: the newest one counts.
        best = versions[0]
        for v in versions[1:]:
            if vercmp.compare(scheme, v, best) > 0:
                best = v
        ok = not p["target"] or vercmp.compare(scheme, best, p["target"]) >= 0
        out.append(check(f"package {name}", ok, f"{best} (planned {p['target'] or 'any'})"))
    return out
