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
if not BASE or not ADMIN_PW:
    sys.exit("usage: SEYALRUN_ADMIN_PASSWORD=... e2e-staging.py https://host:port")

CTX = ssl.create_default_context(cafile=os.environ.get("SEYALRUN_CA_FILE") or None)
if os.environ.get("SEYALRUN_CA_FILE"):
    # Pinned: only the one self-signed cert in the file is trusted. It names the
    # host by CN only (no SAN), so a name match would always fail.
    CTX.check_hostname = False

results: list[bool] = []
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

    s, r = call("POST", f"/api/v1/job-templates/{t['id']}/run", A, {})
    if not check("gated run waits for approval", s in (200, 202) and r.get("status") == "pending_approval", f"{s} {r}"):
        return
    run_id = r["run_id"]

    s, d = call("POST", f"/api/v1/job-runs/{run_id}/approve", A)
    check("F-2 requester cannot approve own run (403)", s == 403, f"{s} {d}")
    _, d = call("GET", f"/api/v1/job-runs/{run_id}", A)
    check("F-2 refused self-approval leaves run pending", d.get("status") == "pending_approval", d.get("status"))

    s, d = call("POST", f"/api/v1/job-runs/{run_id}/approve", B)
    check("F-2 second admin approves (202)", s == 202, f"{s} {d}")
    for _ in range(45):
        _, d = call("GET", f"/api/v1/job-runs/{run_id}", A)
        if d.get("status") in ("success", "failed", "error", "cancelled"):
            break
        time.sleep(1)
    check("approved run executes to success", d.get("status") == "success",
          f"{d.get('status')} {(d.get('output_lines') or [])[-3:]}")

    s, r2 = call("POST", f"/api/v1/job-templates/{t['id']}/run", A, {})
    s, d = call("POST", f"/api/v1/job-runs/{r2['run_id']}/reject", A, {"reason": "e2e withdraw"})
    check("F-2 requester can withdraw own run", s == 200 and (d or {}).get("status") == "rejected", f"{s} {d}")

    time.sleep(2)  # audit forwarding is asynchronous
    ap = audit(A, "job.approve", run_id)
    check("job.approve audited to the approver", len(ap) == 1 and ap[0].get("user_id") == u2["id"], ap)
    st = audit(A, "job.start", run_id)
    check("R-18 job.start audited with bare requester id", len(st) == 1 and st[0].get("user_id") == admin_id, st)
    check("R-18 job.finish audited", len(audit(A, "job.finish", run_id)) == 1)
    rj = audit(A, "job.reject", r2["run_id"])
    check("job.reject audited to the requester", len(rj) == 1 and rj[0].get("user_id") == admin_id, rj)

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
    finally:
        for path, _ in reversed(cleanup):
            call("DELETE", path, A)
    print(f"{sum(results)}/{len(results)} e2e checks passed")
    sys.exit(0 if results and all(results) else 1)
