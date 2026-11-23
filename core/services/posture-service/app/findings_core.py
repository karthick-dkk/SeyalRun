"""Correlation, dedupe and lifecycle of findings — the database-free core.

``app.scanning`` supplies a Store backed by SQLAlchemy; the test suite supplies
an in-memory one. Everything that decides WHAT happens to a finding lives here,
so the rules are tested exactly as they run.

Host correlation (imported results): the inventory host id if the file names
one that exists; else the hostname/FQDN, matched case-insensitively against the
inventory name, an inventory address that is a DNS name, and the hostname facts
reported — first exactly, then by short name (first DNS label); else the IP.
A name or IP that matches MORE than one host is not guessed at: the result goes
to the unmatched queue, exactly like an unknown host. Nothing is ever dropped.

Dedupe key: (host, CVE, package). The package is canonical per host — on a
Debian-family host a binary name (libssl3) is mapped to its source package
(openssl), because that is what advisories and the native matcher use. A record
without a package attaches to every finding of that CVE on the host (or creates
one package-less finding); a later record that does name the package fills it
in rather than creating a second finding.

Lifecycle: open -> fixed when a later native scan no longer sees it (or, for a
finding only imported scanners reported, when facts show the package removed or
upgraded to at least the fixed version); fixed -> open ("reopened") when any
scanner reports it again; accepted stays accepted while it is still reported.
"""

from __future__ import annotations

import ipaddress
import uuid
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Protocol

from libs.pluginbase import HostRef, ScanRecord

from .refs import safe_urls
from .risk import clean_cvss, normalise_severity, risk_score, worse
from .vercmp import compare

NATIVE = "native"
OPEN, FIXED, ACCEPTED = "open", "fixed", "accepted"


def _is_ip(v: str) -> bool:
    try:
        ipaddress.ip_address(v)
        return True
    except ValueError:
        return False


class HostIndex:
    def __init__(self, inventory: Iterable[dict], facts_hostnames: dict[str, str] | None = None):
        self.ids: set[str] = set()
        self.names: dict[str, set[str]] = {}
        self.short: dict[str, set[str]] = {}
        self.ips: dict[str, set[str]] = {}
        for h in inventory:
            hid = str(h.get("id") or "")
            if not hid:
                continue
            self.ids.add(hid)
            self._name(h.get("name"), hid)
            addr = str(h.get("ip") or "").strip()
            if addr:
                if _is_ip(addr):
                    self.ips.setdefault(addr, set()).add(hid)
                else:
                    self._name(addr, hid)
        for hid, name in (facts_hostnames or {}).items():
            if hid in self.ids:
                self._name(name, hid)

    def _name(self, name, hid: str) -> None:
        n = str(name or "").strip().lower().rstrip(".")
        if not n:
            return
        self.names.setdefault(n, set()).add(hid)
        if not _is_ip(n):
            self.short.setdefault(n.split(".", 1)[0], set()).add(hid)

    @staticmethod
    def _one(hits: set[str] | None) -> str | None:
        return next(iter(hits)) if hits and len(hits) == 1 else None

    def resolve(self, ref: HostRef) -> str | None:
        if ref.host_id and ref.host_id in self.ids:
            return ref.host_id
        cands = [c.strip().lower().rstrip(".") for c in (ref.fqdn, ref.hostname) if c and c.strip()]
        for c in cands:
            if c in self.names:
                return self._one(self.names[c])   # ambiguous exact name: do not guess further
        for c in cands:
            if _is_ip(c):
                hit = self._one(self.ips.get(c))
                if hit:
                    return hit
                continue
            s = c.split(".", 1)[0]
            if s in self.short:
                return self._one(self.short[s])
        if ref.ip and ref.ip.strip():
            return self._one(self.ips.get(ref.ip.strip()))
        return None


class Store(Protocol):
    async def load(self, host_id: str, cve_ids: list[str]) -> list[dict]: ...
    async def insert(self, rows: list[dict]) -> None: ...
    async def update(self, rows: list[dict]) -> None: ...
    async def add_events(self, rows: list[dict]) -> None: ...
    async def enrichment(self, cve_ids: list[str]) -> dict[str, tuple[float | None, float | None, bool]]: ...


def _merge_record(acc: dict, rec: ScanRecord) -> None:
    """Fold a duplicate record (same key, same batch) into the first one."""
    sev = normalise_severity(rec.severity, rec.cvss)
    acc["severity"] = worse(acc["severity"], sev)
    c = clean_cvss(rec.cvss)
    if c is not None and (acc["cvss"] is None or c > acc["cvss"]):
        acc["cvss"] = c
    acc["fixed_version"] = acc["fixed_version"] or rec.fixed_version
    acc["installed_version"] = acc["installed_version"] or rec.installed_version
    acc["summary"] = acc["summary"] or rec.summary
    acc["references"] = safe_urls(acc["references"] + list(rec.references or []))


def _event(fid: str, now: datetime, event: str, by: str, details: dict | None = None) -> dict:
    return {"finding_id": fid, "at": now, "event": event[:64], "by": by[:200], "details": details or {}}


async def apply_records(store: Store, host_id: str, records: Iterable[ScanRecord], scanner: str,
                        now: datetime, canon: Callable[[str], str] = lambda p: p,
                        actor: str = "") -> tuple[int, int, set[tuple[str, str]]]:
    """Upsert one host's records from one scanner. Returns (created, updated,
    the (cve, package) keys seen)."""
    by = actor or scanner
    incoming: dict[tuple[str, str], dict] = {}
    for rec in records:
        if not rec.cve_id:
            continue
        pkg = canon(rec.package or "")[:255]
        key = (rec.cve_id[:64], pkg)
        if key in incoming:
            _merge_record(incoming[key], rec)
            continue
        cvss = clean_cvss(rec.cvss)
        incoming[key] = {
            "severity": normalise_severity(rec.severity, cvss), "cvss": cvss,
            "fixed_version": (rec.fixed_version or None), "installed_version": rec.installed_version or "",
            "summary": rec.summary, "references": safe_urls(rec.references),
        }
    if not incoming:
        return 0, 0, set()
    cves = sorted({k[0] for k in incoming})
    existing = await store.load(host_id, cves)
    by_key: dict[tuple[str, str], dict] = {(r["cve_id"], r["package"]): r for r in existing}
    by_cve: dict[str, list[dict]] = {}
    for r in existing:
        by_cve.setdefault(r["cve_id"], []).append(r)
    enrich = await store.enrichment(cves)

    new_rows: list[dict] = []
    touched: dict[str, dict] = {}
    events: list[dict] = []

    def score(row: dict) -> None:
        epss, pct, kev = enrich.get(row["cve_id"], (None, None, False))
        row["epss"], row["epss_percentile"], row["kev"] = epss, pct, bool(kev)
        row["risk_score"] = risk_score(row["cvss"], row["severity"], pct, bool(kev))

    def merge_into(row: dict, inc: dict, pkg: str) -> None:
        if pkg and not row["package"]:
            row["package"] = pkg
            events.append(_event(row["id"], now, "package_identified", by, {"package": pkg}))
        sources = list(row.get("sources") or [])
        if scanner not in sources:
            sources.append(scanner)
            events.append(_event(row["id"], now, "reported", by, {"scanner": scanner}))
        row["sources"] = sources
        row["last_seen"] = now
        if row["status"] == FIXED:
            row["status"] = OPEN
            row["fixed_at"] = None
            events.append(_event(row["id"], now, "reopened", by, {"scanner": scanner}))
        row["severity"] = worse(row["severity"], inc["severity"])
        if inc["cvss"] is not None and (row["cvss"] is None or inc["cvss"] > row["cvss"]):
            row["cvss"] = inc["cvss"]
        # The native matcher reads the host's real package state: it is
        # authoritative for versions. Imports only fill what is missing.
        if scanner == NATIVE:
            row["installed_version"] = inc["installed_version"] or row["installed_version"]
            row["fixed_version"] = inc["fixed_version"] or row["fixed_version"]
        else:
            row["installed_version"] = row["installed_version"] or inc["installed_version"]
            row["fixed_version"] = row["fixed_version"] or inc["fixed_version"]
        row["summary"] = row["summary"] or inc["summary"]
        row["references"] = safe_urls(list(row.get("references") or []) + inc["references"])
        score(row)
        touched[row["id"]] = row

    for (cve_id, pkg), inc in incoming.items():
        targets: list[dict] = []
        if pkg:
            hit = by_key.get((cve_id, pkg))
            if hit is None:
                bare = by_key.pop((cve_id, ""), None)
                if bare is not None:
                    hit = bare
                    by_key[(cve_id, pkg)] = bare
            if hit is not None:
                targets = [hit]
        else:
            targets = list(by_cve.get(cve_id) or [])
        if targets:
            for row in targets:
                merge_into(row, inc, pkg)
            continue
        row = {
            "id": str(uuid.uuid4()), "host_id": host_id, "cve_id": cve_id, "package": pkg,
            "installed_version": inc["installed_version"][:255], "fixed_version": inc["fixed_version"],
            "severity": inc["severity"], "cvss": inc["cvss"], "summary": inc["summary"],
            "references": inc["references"], "sources": [scanner], "first_seen": now, "last_seen": now,
            "status": OPEN, "status_reason": None, "fixed_at": None,
        }
        score(row)
        new_rows.append(row)
        by_key[(cve_id, pkg)] = row
        by_cve.setdefault(cve_id, []).append(row)
        events.append(_event(row["id"], now, "detected", by, {"scanner": scanner}))

    if new_rows:
        await store.insert(new_rows)
    created_ids = {r["id"] for r in new_rows}
    updates = [r for fid, r in touched.items() if fid not in created_ids]
    if updates:
        await store.update(updates)
    if events:
        await store.add_events(events)
    return len(new_rows), len(updates), set(incoming)


def fixed_after_native(rows: Iterable[dict], seen: set[tuple[str, str]], installed: dict[str, str],
                       scheme: str | None, native_trusted: bool) -> list[tuple[dict, str]]:
    """Which open/accepted findings of ONE host a completed native scan proves
    fixed, with the reason. ``native_trusted`` is False when the active feed
    holds no advisories for the host's distro — then "not seen" means "no data"
    and only the facts-based rule applies. ``installed`` empty (no facts) means
    nothing can be proven."""
    out: list[tuple[dict, str]] = []
    if not installed:
        return out
    for r in rows:
        if r["status"] not in (OPEN, ACCEPTED):
            continue
        key = (r["cve_id"], r["package"])
        if NATIVE in (r.get("sources") or []):
            if native_trusted and key not in seen:
                out.append((r, "no longer detected by the native scan"))
            continue
        pkg = r.get("package") or ""
        if not pkg or scheme is None:
            continue
        have = installed.get(pkg)
        if have is None:
            out.append((r, f"package {pkg} is no longer installed"))
        elif r.get("fixed_version") and compare(scheme, have, r["fixed_version"]) >= 0:
            out.append((r, f"installed {have} >= fixed {r['fixed_version']}"))
    return out


def group_unmatched(records: Iterable[tuple[HostRef, ScanRecord]]) -> dict[tuple, dict]:
    """Records whose host did not resolve, grouped per reported host."""
    groups: dict[tuple, dict] = {}
    for ref, rec in records:
        g = groups.setdefault(ref.key(), {"ref": ref, "records": []})
        g["records"].append(rec)
    return groups


def reported_host_label(ref: HostRef) -> str:
    return (ref.fqdn or ref.hostname or ref.host_id or ref.ip or "(no host in file)")[:255]
