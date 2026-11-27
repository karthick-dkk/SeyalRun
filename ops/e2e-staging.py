#!/usr/bin/env python3
"""Behavioural end-to-end checks against a running SeyalRun stack.

    SEYALRUN_ADMIN_PASSWORD=... python3 ops/e2e-staging.py https://<host>:<port>

Called by ops/verify-staging.sh (one PASS/FAIL line per check, exit 1 on any
FAIL). Each check drives the real API and asserts on the outcome a user would
see, not on a proxy for it:

  * F-2  the requester of a gated job run cannot approve it; another admin can,
         and the approved run actually executes; the requester can withdraw.
  * R-18 job.start / job.finish audit rows exist and carry the bare user id.
  * F-12 the MCP ack_notification tool marks the notification read.
  * 3.0 M1 posture: reads answer, every call lands in the audit chain, an
         unsigned/garbage feed bundle is refused (400) AND audited, a
         posture:read PAT reads but cannot collect. Optional, when the staging
         box is set up for it:
           POSTURE_E2E_BUNDLE=/path/bundle.tar.gz  a bundle signed by a key in
             the box's POSTURE_FEED_TRUSTED_KEYS: imports, becomes ready,
             activates, and the summary reports its version;
           POSTURE_E2E_HOST_IDS=<id>,<id>  e.g. one Debian- and one RHEL-family
             inventory host with a working credential: facts are collected and
             show a distro family + package count for each.
  * 3.0 M2 findings: findings / summary / unmatched / scanners answer in the
         contract shape and are audited, a malformed scanner file is refused
         (400) and audited, a posture:read PAT reads findings but cannot import.
         With POSTURE_E2E_HOST_IDS: the same CVE imported from two scanners
         (generic CSV + Vuls JSON) is ONE finding on that host listing both; a
         javascript: reference in the file never comes back; a result for an
         unknown host lands in the unmatched queue and assigning it creates the
         finding; accept/reopen round-trips and is audited; a native run is
         accepted. The CVE used is a fixed fake id, so repeated runs update one
         finding instead of piling up.

  * 3.0 M3/M4 patching: lists answer and are audited; a mirror without a signing key,
         a misspelt safety setting and a rollout with no window choice are refused
         (400); a posture:read PAT reads rollouts but cannot create one. Optional:
           POSTURE_E2E_PATCH_HOST_ID=<id> POSTURE_E2E_PATCH_PACKAGE=<name>  one host
             that may really be patched: a rollout of that package (reboot policy
             "never") is refused self-approval (403) and a wrong plan hash (409),
             approved by the second admin, runs to completed, and its evidence names
             both people. Without them nothing is changed on any host.

Everything it creates (second admin, project, template, PAT) is removed at the
end, so repeated runs do not pile up on staging. Standard library only.

TLS: staging edges use a self-signed certificate. verify-staging.sh fetches it
over SSH and passes it as SEYALRUN_CA_FILE, which pins that exact certificate
(the cert is the trust anchor, so the hostname check is not needed). Without a
CA file the system trust store is used, as for a properly issued cert.
"""
from __future__ import annotations

import json
import os
import secrets
import ssl
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else ""
ADMIN_PW = os.environ.get("SEYALRUN_ADMIN_PASSWORD", "")
# The deployment's SEYALRUN_ADMIN_APPROVAL (verify-staging.sh reads it from the remote .env).
# Same rule as the services: "off" (also when unset) or "on"; anything else counts as "on".
ADMIN_APPROVAL_ON = os.environ.get("SEYALRUN_ADMIN_APPROVAL", "off").strip().lower() != "off"
if not BASE or not ADMIN_PW:
    sys.exit("usage: SEYALRUN_ADMIN_PASSWORD=... e2e-staging.py https://host:port")

CTX = ssl.create_default_context(cafile=os.environ.get("SEYALRUN_CA_FILE") or None)
if os.environ.get("SEYALRUN_CA_FILE"):
    # Pinned: only the one self-signed cert in the file is trusted. It names the
    # host by CN only (no SAN), so a name match would always fail.
    CTX.check_hostname = False

results: list[bool] = []
PAT: dict = {}  # posture:read token issued by posture_checks, reused by findings_checks
cleanup: list[tuple[str, str]] = []  # (path, token-name) deleted in reverse order


def call(method, path, token=None, body=None):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw.decode(errors="replace")


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:240]}]" if detail and not cond else ""))
    return bool(cond)


def login(user, pw):
    s, d = call("POST", "/api/v1/auth/login", body={"username": user, "password": pw})
    if s != 200 or not isinstance(d, dict) or not d.get("access_token"):
        raise RuntimeError(f"login {user} -> {s} {d}")
    return d["access_token"]


def audit(token, action, resource_id):
    _, logs = call("GET", "/api/v1/audit/logs?limit=200", token)
    items = logs if isinstance(logs, list) else (logs or {}).get("items") or (logs or {}).get("logs") or []
    return [entry for entry in items if entry.get("action") == action and entry.get("resource_id") == resource_id]


def main(A):
    _, me = call("GET", "/api/v1/auth/session", A)
    admin_id = (me.get("user") or me)["id"]

    # A second admin: the only principal allowed to approve the first one's run.
    _, roles = call("GET", "/api/v1/roles", A)
    admin_role = next(r["id"] for r in roles if r["name"] == "admin")
    uname, pw = "e2e-approver-" + secrets.token_hex(3), "E2e-" + secrets.token_urlsafe(12)
    s, u2 = call("POST", "/api/v1/users", A, {"username": uname, "password": pw, "role_ids": [admin_role]})
    if not check("create second admin", s in (200, 201), f"{s} {u2}"):
        return
    cleanup.append((f"/api/v1/users/{u2['id']}", "A"))
    B = login(uname, pw)
    s, d = call("POST", "/api/v1/auth/change-password", B, {"current_password": pw, "new_password": pw + "x!"})
    if s == 200:
        B = (d or {}).get("access_token") or login(uname, pw + "x!")
    PAT["admin2"] = B  # reused by patching_checks: the second person who approves

    s, proj = call("POST", "/api/v1/projects", A, {"name": "e2e-" + secrets.token_hex(3), "source_type": "local"})
    if not check("create project", s in (200, 201), f"{s} {proj}"):
        return
    cleanup.append((f"/api/v1/projects/{proj['id']}", "A"))
    s, t = call("POST", "/api/v1/job-templates", A, {
        "project_id": proj["id"], "name": "e2e-gated-" + secrets.token_hex(3), "action_type": "bash_script",
        "script_content": "echo e2e-ok", "default_params": {"run_local": True}, "requires_approval": True})
    if not check("create approval-gated job template", s in (200, 201), f"{s} {t}"):
        return
    cleanup.append((f"/api/v1/job-templates/{t['id']}", "A"))

    # A non-admin's gated run waits for an admin in both modes of SEYALRUN_ADMIN_APPROVAL.
    sup_role = next(r["id"] for r in roles if r["name"] == "support")
    sname, spw = "e2e-support-" + secrets.token_hex(3), "E2e-" + secrets.token_urlsafe(12)
    s, su = call("POST", "/api/v1/users", A, {"username": sname, "password": spw, "role_ids": [sup_role]})
    if not check("create support user", s in (200, 201), f"{s} {su}"):
        return
    cleanup.append((f"/api/v1/users/{su['id']}", "A"))
    S = login(sname, spw)
    s, d = call("POST", "/api/v1/auth/change-password", S, {"current_password": spw, "new_password": spw + "x!"})
    if s == 200:
        S = (d or {}).get("access_token") or login(sname, spw + "x!")

    s, r = call("POST", f"/api/v1/job-templates/{t['id']}/run", S, {})
    if not check("support's gated run waits for approval", s in (200, 202) and r.get("status") == "pending_approval", f"{s} {r}"):
        return
    run_id = r["run_id"]

    s, d = call("POST", f"/api/v1/job-runs/{run_id}/approve", S)
    check("F-2 requester cannot approve own run (403)", s == 403, f"{s} {d}")
    _, d = call("GET", f"/api/v1/job-runs/{run_id}", A)
    check("F-2 refused self-approval leaves run pending", d.get("status") == "pending_approval", d.get("status"))

    s, d = call("POST", f"/api/v1/job-runs/{run_id}/approve", B)
    check("F-2 an admin approves (202)", s == 202, f"{s} {d}")
    for _ in range(45):
        _, d = call("GET", f"/api/v1/job-runs/{run_id}", A)
        if d.get("status") in ("success", "failed", "error", "cancelled"):
            break
        time.sleep(1)
    check("approved run executes to success", d.get("status") == "success",
          f"{d.get('status')} {(d.get('output_lines') or [])[-3:]}")

    s, r2 = call("POST", f"/api/v1/job-templates/{t['id']}/run", S, {})
    s, d = call("POST", f"/api/v1/job-runs/{r2['run_id']}/reject", S, {"reason": "e2e withdraw"})
    check("F-2 requester can withdraw own run", s == 200 and (d or {}).get("status") == "rejected", f"{s} {d}")

    # An admin's own gated run: waits when SEYALRUN_ADMIN_APPROVAL=on, runs at once when off.
    s, r3 = call("POST", f"/api/v1/job-templates/{t['id']}/run", A, {})
    if ADMIN_APPROVAL_ON:
        check("switch on: admin's gated run waits for approval", s in (200, 202) and r3.get("status") == "pending_approval", f"{s} {r3}")
        call("POST", f"/api/v1/job-runs/{r3['run_id']}/reject", A, {"reason": "e2e withdraw"})
    else:
        check("switch off: admin's gated run starts without approval", s in (200, 202) and r3.get("status") != "pending_approval", f"{s} {r3}")
        for _ in range(45):
            _, d3 = call("GET", f"/api/v1/job-runs/{r3['run_id']}", A)
            if d3.get("status") in ("success", "failed", "error", "cancelled"):
                break
            time.sleep(1)
        check("switch off: admin's run executes to success", d3.get("status") == "success", d3.get("status"))

    time.sleep(2)  # audit forwarding is asynchronous
    ap = audit(A, "job.approve", run_id)
    check("job.approve audited to the approver", len(ap) == 1 and ap[0].get("user_id") == u2["id"], ap)
    st = audit(A, "job.start", run_id)
    check("R-18 job.start audited with bare requester id", len(st) == 1 and st[0].get("user_id") == su["id"], st)
    check("R-18 job.finish audited", len(audit(A, "job.finish", run_id)) == 1)
    rj = audit(A, "job.reject", r2["run_id"])
    check("job.reject audited to the requester", len(rj) == 1 and rj[0].get("user_id") == su["id"], rj)
    if not ADMIN_APPROVAL_ON:
        st3 = audit(A, "job.start", r3["run_id"])
        check("switch off: job.start says approval was not required",
              len(st3) == 1 and st3[0].get("user_id") == admin_id and "not required (SEYALRUN_ADMIN_APPROVAL=off)" in json.dumps(st3[0]), st3)

    # F-12 — through the MCP endpoint, with a PAT scoped to exactly what it needs.
    s, pat = call("POST", "/api/v1/api-tokens", A, {"name": "e2e-mcp", "scopes": ["notifications:read", "notifications:ack"]})
    if not check("issue notifications-scoped PAT", s == 201, f"{s} {pat}"):
        return
    cleanup.append((f"/api/v1/api-tokens/{pat['id']}", "A"))

    def mcp(method, params, i):
        return call("POST", "/mcp", pat["token"], {"jsonrpc": "2.0", "id": i, "method": method, "params": params})

    s, d = mcp("tools/call", {"name": "list_notifications", "arguments": {"limit": 50}}, 1)
    ok = s == 200 and isinstance(d, dict) and "error" not in d and not d.get("result", {}).get("isError")
    if not check("MCP list_notifications", ok, f"{s} {d}"):
        return
    listed = json.loads(d["result"]["content"][0]["text"])
    items = listed.get("items", listed) if isinstance(listed, dict) else listed
    unread = [n for n in items if not n.get("read")]
    if not check("an unread notification exists to ack (the approval created one)", bool(unread), items[:3]):
        return
    nid = unread[0]["id"]
    s, d = mcp("tools/call", {"name": "ack_notification", "arguments": {"notification_id": nid}}, 2)
    check("F-12 MCP ack_notification succeeds", s == 200 and "error" not in d and not d.get("result", {}).get("isError"), f"{s} {d}")
    _, n2 = call("GET", "/api/v1/notifications?limit=200", A)
    row = next((n for n in (n2 or {}).get("items", []) if n["id"] == nid), {})
    check("F-12 notification is now marked read", row.get("read") is True, row)


def call_multipart(path, token, filename, data: bytes, fields=None):
    boundary = "----e2e" + secrets.token_hex(8)
    body = b"".join(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
                    for k, v in (fields or {}).items())
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
             f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(BASE + path, method="POST", data=body)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw.decode(errors="replace")


def audit_any(token, action, since_ids=()):
    _, logs = call("GET", "/api/v1/audit/logs?limit=200", token)
    items = logs if isinstance(logs, list) else (logs or {}).get("items") or (logs or {}).get("logs") or []
    return [e for e in items if e.get("action") == action and e.get("id") not in since_ids]


def posture_checks(A):
    """SeyalRun 3.0 M1 — posture-service through the real gateway."""
    for path in ("/api/v1/posture/summary", "/api/v1/posture/hosts", "/api/v1/posture/feeds"):
        s, d = call("GET", path, A)
        if not check(f"posture GET {path} (200)", s == 200, f"{s} {d}"):
            return
    _, summary = call("GET", "/api/v1/posture/summary", A)
    check("posture summary has the contract fields",
          {"hosts_total", "hosts_reporting", "reboot_required", "security_updates",
           "active_feed_version", "feed_age_days"} <= set(summary or {}), summary)
    time.sleep(2)  # audit forwarding is asynchronous
    check("posture reads are audited (posture.summary.view)", bool(audit_any(A, "posture.summary.view")))
    check("posture reads are audited (posture.hosts.list)", bool(audit_any(A, "posture.hosts.list")))

    before = {e.get("id") for e in audit_any(A, "posture.feed.import")}
    s, d = call_multipart("/api/v1/posture/feeds/import", A, "garbage.tar.gz", secrets.token_bytes(4096))
    check("unsigned/garbage feed bundle refused (400)", s == 400 and isinstance(d, dict) and d.get("detail"), f"{s} {d}")
    time.sleep(2)
    rows = audit_any(A, "posture.feed.import", before)
    check("refused feed import is audited as a failure", any(r.get("result") == "failure" for r in rows), rows[:2])

    s, pat = call("POST", "/api/v1/api-tokens", A, {"name": "e2e-posture", "scopes": ["posture:read"]})
    if check("issue posture:read PAT", s == 201, f"{s} {pat}"):
        cleanup.append((f"/api/v1/api-tokens/{pat['id']}", "A"))
        s, _ = call("GET", "/api/v1/posture/hosts", pat["token"])
        check("posture:read PAT reads hosts (200)", s == 200, s)
        s, _ = call("POST", "/api/v1/posture/collect", pat["token"])
        check("posture:read PAT cannot collect (403)", s == 403, s)
        PAT["token"] = pat["token"]

    bundle = os.environ.get("POSTURE_E2E_BUNDLE")
    if bundle:
        data = open(bundle, "rb").read()
        s, snap = call_multipart("/api/v1/posture/feeds/import", A, os.path.basename(bundle), data)
        if s == 409:  # already imported on an earlier run — find it
            _, all_feeds = call("GET", "/api/v1/posture/feeds", A)
            snap = next((f for f in all_feeds if f.get("status") == "ready"), None)
            s = 201 if snap else s
        if check("signed feed bundle imports offline (201)", s == 201, f"{s} {snap}"):
            for _ in range(120):
                _, all_feeds = call("GET", "/api/v1/posture/feeds", A)
                snap = next(f for f in all_feeds if f["id"] == snap["id"])
                if snap.get("status") != "importing":
                    break
                time.sleep(1)
            check("feed snapshot parsed to ready", snap.get("status") == "ready", snap)
            check("feed snapshot carries per-source entry counts",
                  all(src["entries"] > 0 for src in snap.get("sources") or [{"entries": 0}]), snap.get("sources"))
            s, act = call("POST", f"/api/v1/posture/feeds/{snap['id']}/activate", A)
            check("feed snapshot activates (200)", s == 200 and (act or {}).get("active") is True, f"{s} {act}")
            _, summary = call("GET", "/api/v1/posture/summary", A)
            check("summary reports the active feed version", summary.get("active_feed_version") == snap["version"], summary)
    else:
        print("NOTE POSTURE_E2E_BUNDLE not set — skipping signed-bundle import/activate")

    host_ids = [h for h in os.environ.get("POSTURE_E2E_HOST_IDS", "").split(",") if h]
    for hid in host_ids:
        s, d = call("POST", f"/api/v1/posture/hosts/{hid}/collect", A)
        check(f"posture collect {hid[:8]} accepted (202)", s == 202 and (d or {}).get("job_run_id"), f"{s} {d}")
    for hid in host_ids:
        h = {}
        for _ in range(60):
            _, h = call("GET", f"/api/v1/posture/hosts/{hid}", A)
            if (h or {}).get("collect_state") == "idle" and h.get("status") in ("ok", "failed"):
                break
            time.sleep(3)
        check(f"facts for {hid[:8]}: {h.get('distro')} {h.get('distro_version')} ({h.get('distro_family')})",
              h.get("status") == "ok" and h.get("distro_family") in ("debian", "rhel")
              and h.get("packages_installed", 0) > 0 and h.get("packages_installed_list"),
              {k: h.get(k) for k in ("status", "last_error", "collect_state", "distro_family", "packages_installed")})
    if not host_ids:
        print("NOTE POSTURE_E2E_HOST_IDS not set — skipping live facts collection")


E2E_CVE = "CVE-1999-99001"   # fake, fixed: repeated runs update one finding


def findings_checks(A, pat_token=None):
    """SeyalRun 3.0 M2 — findings through the real gateway."""
    s, f = call("GET", "/api/v1/posture/findings?limit=5", A)
    if not check("findings GET (200, {items, total})", s == 200 and {"items", "total"} <= set(f or {}), f"{s} {f}"):
        return
    s, sm = call("GET", "/api/v1/posture/findings/summary", A)
    check("findings summary has the contract fields", s == 200 and {
        "open_total", "by_severity", "kev_open", "hosts_affected", "feed_stale", "feed_age_days",
        "stale_after_days"} <= set(sm or {}), f"{s} {sm}")
    s, ps = call("GET", "/api/v1/posture/summary", A)
    check("posture summary carries findings_open + kev_open", {"findings_open", "kev_open"} <= set(ps or {}), ps)
    s, um = call("GET", "/api/v1/posture/unmatched", A)
    check("unmatched GET (200)", s == 200 and "items" in (um or {}), f"{s} {um}")
    s, sc = call("GET", "/api/v1/posture/scanners", A)
    names = {x.get("name") for x in sc} if isinstance(sc, list) else set()
    check("scanners: native, generic, openscap, vuls discovered", {"native", "generic", "openscap", "vuls"} <= names, sc)
    time.sleep(2)
    check("findings reads are audited (posture.findings.list)", bool(audit_any(A, "posture.findings.list")))

    before = {e.get("id") for e in audit_any(A, "posture.scan.import")}
    s, d = call_multipart("/api/v1/posture/scans/import", A, "bad.xml", b"<html>nope</html>", {"scanner": "openscap"})
    check("malformed OpenSCAP file refused (400)", s == 400 and isinstance(d, dict) and d.get("detail"), f"{s} {d}")
    time.sleep(2)
    check("refused scan import is audited as a failure",
          any(r.get("result") == "failure" for r in audit_any(A, "posture.scan.import", before)))
    if pat_token:
        s, _ = call("GET", "/api/v1/posture/findings", pat_token)
        check("posture:read PAT reads findings (200)", s == 200, s)
        s, _ = call_multipart("/api/v1/posture/scans/import", pat_token, "x.csv", b"cve\n", {"scanner": "generic"})
        check("posture:read PAT cannot import (403)", s == 403, s)

    host_ids = [h for h in os.environ.get("POSTURE_E2E_HOST_IDS", "").split(",") if h]
    if not host_ids:
        print("NOTE POSTURE_E2E_HOST_IDS not set — skipping the two-scanner dedupe / unmatched / status checks")
        return
    hid = host_ids[0]
    csv_body = (f"host_id,cve,package,installed_version,fixed_version,severity,cvss,references\n"
                f"{hid},{E2E_CVE},e2e-dummy-pkg,1.0-1,1.0-2,high,7.5,"
                f"https://example.org/e2e javascript:alert(1)\n").encode()
    s, d = call_multipart("/api/v1/posture/scans/import", A, "e2e.csv", csv_body, {"scanner": "generic"})
    check("generic CSV import (201)", s == 201 and (d or {}).get("unmatched") == 0, f"{s} {d}")
    vuls = {"serverName": "e2e-not-a-real-host", "scannedCves": {E2E_CVE: {
        "cveID": E2E_CVE, "affectedPackages": [{"name": "e2e-dummy-pkg", "fixedIn": "1.0-2"}],
        "cveContents": {"nvd": [{"cvss3Score": 7.5, "summary": "SeyalRun e2e fixture"}]}}}, "packages": {}}
    s, d = call_multipart("/api/v1/posture/scans/import", A, "e2e-vuls.json", json.dumps(vuls).encode(),
                          {"scanner": "vuls", "host_id": hid})
    check("vuls JSON import with host override (201)", s == 201, f"{s} {d}")
    _, f = call("GET", f"/api/v1/posture/findings?host_id={hid}&q={E2E_CVE}", A)
    items = (f or {}).get("items") or []
    check("same CVE from two scanners is ONE finding on the right host",
          len(items) == 1 and items[0].get("host_id") == hid and {"generic", "vuls"} <= set(items[0].get("sources") or []),
          items)
    if len(items) != 1:
        return
    fid = items[0]["id"]
    _, det = call("GET", f"/api/v1/posture/findings/{fid}", A)
    refs = (det or {}).get("references") or []
    check("javascript: reference from the file is not served", refs and all(
        r.startswith(("http://", "https://")) for r in refs), refs)
    check("finding detail carries history", bool((det or {}).get("history")), det)

    ghost = f"e2e-ghost-{secrets.token_hex(4)}.invalid"
    s, d = call_multipart("/api/v1/posture/scans/import", A, "ghost.csv",
                          f"hostname,cve,package\n{ghost},{E2E_CVE},e2e-dummy-pkg\n".encode(), {"scanner": "generic"})
    check("unknown host goes to the unmatched queue (unmatched=1)", s == 201 and (d or {}).get("unmatched") == 1, f"{s} {d}")
    _, um = call("GET", "/api/v1/posture/unmatched?limit=500", A)
    row = next((u for u in (um or {}).get("items") or [] if u.get("reported_host") == ghost), None)
    if check("unmatched result is listed, not dropped", row is not None and row.get("cve_count") == 1, um):
        s, d = call("POST", f"/api/v1/posture/unmatched/{row['id']}/assign", A, {"host_id": hid})
        check("assigning the unmatched result succeeds", s == 200 and "created_findings" in (d or {}), f"{s} {d}")
        _, um2 = call("GET", "/api/v1/posture/unmatched?limit=500", A)
        check("assigned result leaves the queue",
              not any(u.get("id") == row["id"] for u in (um2 or {}).get("items") or []), um2)

    s, d = call("POST", f"/api/v1/posture/findings/{fid}/status", A, {"status": "accepted"})
    check("accepting without a reason is refused (400)", s == 400, f"{s} {d}")
    s, d = call("POST", f"/api/v1/posture/findings/{fid}/status", A, {"status": "accepted", "reason": "e2e"})
    check("accept with reason (200)", s == 200 and (d or {}).get("status") == "accepted", f"{s} {d}")
    s, d = call("POST", f"/api/v1/posture/findings/{fid}/status", A, {"status": "open", "reason": "e2e reopen"})
    check("reopen (200)", s == 200 and (d or {}).get("status") == "open", f"{s} {d}")
    time.sleep(2)
    check("status change is audited", bool(audit_any(A, "posture.finding.status")))
    s, d = call("POST", "/api/v1/posture/scanners/native/run", A, {"host_ids": host_ids})
    check("native run accepted (202)", s == 202 and "queued" in (d or {}), f"{s} {d}")


def patching_checks(A, pat_token=None):
    """SeyalRun 3.0 M3/M4 — patching through the real gateway."""
    base = "/api/v1/posture"
    for ep in ("patches", "patch-baselines", "patch-windows", "patch-repos", "package-ops"):
        s, d = call("GET", f"{base}/{ep}", A)
        if not check(f"patching GET {ep} (200)", s == 200, f"{s} {d}"):
            return
    s, d = call("POST", f"{base}/patch-repos", A, {"slug": "e2e-unsigned", "family": "deb", "base_url": "http://mirror.invalid/d",
                                                  "suite": "x", "components": "main", "gpg_key": ""})
    check("a mirror without a signing key is refused (400)", s == 400, f"{s} {d}")
    s, d = call("POST", f"{base}/patch-baselines", A, {"name": "e2e-bad", "config": {"max_failuers": 1}})
    check("a misspelt safety setting is refused (400)", s == 400, f"{s} {d}")
    s, d = call("POST", f"{base}/patches", A, {"name": "e2e", "host_ids": ["nope"]})
    check("a rollout without a window choice is refused (400)", s == 400 and "window" in str(d), f"{s} {d}")
    s, w = call("POST", f"{base}/patch-windows", A, {"name": f"e2e-{secrets.token_hex(4)}", "timezone": "Asia/Kolkata", "days": [5, 6],
                                                    "start": "22:00", "duration_minutes": 240})
    if check("window created, next opening computed (201)", s == 201 and (w.get("open_now") or w.get("next_open")), f"{s} {w}"):
        s, _ = call("DELETE", f"{base}/patch-windows/{w['id']}", A)
        check("window deleted (204)", s == 204, s)
    time.sleep(2)
    check("patching changes are audited (posture.patch_window.create)", bool(audit_any(A, "posture.patch_window.create")))
    if pat_token:
        s, _ = call("GET", f"{base}/patches", pat_token)
        check("posture:read PAT reads rollouts (200)", s == 200, s)
        s, _ = call("POST", f"{base}/patches", pat_token, {"name": "x", "host_ids": ["x"], "ignore_window": True})
        check("posture:read PAT cannot create a rollout (403)", s == 403, s)

    host, pkg = os.environ.get("POSTURE_E2E_PATCH_HOST_ID"), os.environ.get("POSTURE_E2E_PATCH_PACKAGE")
    if not (host and pkg and PAT.get("admin2")):
        print("NOTE POSTURE_E2E_PATCH_HOST_ID / _PACKAGE not set — skipping the live rollout (no host is changed)")
        return
    s, r = call("POST", f"{base}/patches", A, {"name": "e2e live rollout", "host_ids": [host], "ignore_window": True,
                                              "config": {"scope": "packages", "include": [pkg], "reboot": "never", "soak_minutes": 0}})
    if not check("live rollout created, waiting for approval (201)", s == 201 and r.get("status") == "pending_approval", f"{s} {r}"):
        return
    rid, B = r["id"], PAT["admin2"]
    s, _ = call("POST", f"{base}/patches/{rid}/approve", A, {"plan_hash": r["plan_hash"]})
    check("the requester cannot approve the rollout (403)", s == 403, s)
    s, _ = call("POST", f"{base}/patches/{rid}/approve", B, {"plan_hash": "0" * 64})
    check("approval of a plan that was not reviewed is refused (409)", s == 409, s)
    s, d = call("POST", f"{base}/patches/{rid}/approve", B, {"plan_hash": r["plan_hash"]})
    check("a second admin approves (200)", s == 200, f"{s} {d}")
    for _ in range(180):
        _, d = call("GET", f"{base}/patches/{rid}", A)
        if d.get("status") in ("completed", "halted", "cancelled"):
            break
        time.sleep(5)
    if not check("live rollout completed", d.get("status") == "completed", f"{d.get('status')} {d.get('status_reason')}"):
        call("POST", f"{base}/patches/{rid}/cancel", A, {"reason": "e2e clean-up"})
    row = (d.get("host_rows") or [{}])[0]
    check("the planned package changed on the host", any(c.get("name") == pkg and c.get("new") for c in row.get("changes") or []), row)
    s, ev = call("GET", f"{base}/patches/{rid}/evidence", A)
    check("evidence names requester and approver", s == 200 and ev.get("requested_by") and ev.get("approved_by")
          and ev["requested_by"] != ev["approved_by"], ev if s != 200 else "")


if __name__ == "__main__":
    try:
        A = login("Admin", ADMIN_PW)
    except Exception as exc:
        print(f"FAIL admin login ({exc})")
        sys.exit(1)
    try:
        main(A)
    except Exception as exc:  # a crash is a failure, never a silent pass
        check("e2e completed without an exception", False, repr(exc))
    try:
        posture_checks(A)
    except Exception as exc:
        check("posture e2e completed without an exception", False, repr(exc))
    try:
        patching_checks(A, PAT.get("token"))
    except Exception as exc:
        check("patching e2e completed without an exception", False, repr(exc))
    try:
        findings_checks(A, PAT.get("token"))
    except Exception as exc:
        check("findings e2e completed without an exception", False, repr(exc))
    finally:
        for path, _ in reversed(cleanup):
            call("DELETE", path, A)
    print(f"{sum(results)}/{len(results)} e2e checks passed")
    sys.exit(0 if results and all(results) else 1)
