"""WebSocket SSH terminal handler — adapted from v1 services/zabbix-automation/app/ws/ssh_terminal.py.

Message protocol (JSON over text WebSocket frames):
  client → server: {"type": "input",   "data": "<text>"}
                   {"type": "resize",  "rows": N, "cols": N}
                   {"type": "ping"}
                   {"type": "confirm", "allow": true|false}   (response to confirm_required)
  server → client: {"type": "output",          "data": "<text>"}
                   {"type": "pong"}
                   {"type": "confirm_required", "command": "<cmd>", "filter": "<name>", "filter_id": "<id>"}
                   {"type": "error",            "message": "<text>"}
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import asyncssh
import httpx
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.servicetoken import ServiceTokenError, mint, verify

from ..config import get_settings
from ..database import SessionLocal
from ..models import ZASSHSession, ZASessionCommand
from ._lineguard import clear_remote_line as _clear_remote_line

logger = logging.getLogger(__name__)

_warned_no_hostkey = False


class TeeWebSocket:
    """Wraps the primary session WebSocket so every send_text call also fans out
    to any connected spectators (PCI DSS Phase D: live session viewer) — no
    changes needed to this file's many existing output-send call sites, main.py
    just passes this wrapper into handle_terminal() instead of the raw socket.
    Only "output" frames are teed; pings/errors/confirm-prompts are this
    session's own control channel, not something a spectator should see."""

    def __init__(self, primary, session_id: str, spectators: dict[str, list]):
        self._primary = primary
        self._session_id = session_id
        self._spectators = spectators

    async def send_text(self, data: str) -> None:
        await self._primary.send_text(data)
        subs = self._spectators.get(self._session_id)
        if not subs:
            return
        try:
            frame = json.loads(data)
        except (TypeError, ValueError):
            return
        if not (isinstance(frame, dict) and frame.get("type") == "output"):
            return

        async def _send(ws) -> None:
            try:
                await ws.send_text(data)
            except Exception:
                pass

        # Concurrent, not sequential — a slow/stalled spectator's send must not
        # delay delivery to every other spectator watching the same session.
        await asyncio.gather(*(_send(spectator) for spectator in list(subs)))

    def __getattr__(self, name):
        return getattr(self._primary, name)


def _known_hosts():
    """known_hosts path (strict verification) if SSH_KNOWN_HOSTS_PATH is set, else
    None (accept any key) with a one-time warning. See automation _ssh_exec._known_hosts."""
    global _warned_no_hostkey
    path = (get_settings().ssh_known_hosts_path or "").strip()
    if path:
        return path
    if not _warned_no_hostkey:
        logger.warning("SSH host-key verification disabled (SSH_KNOWN_HOSTS_PATH unset) — MITM risk")
        _warned_no_hostkey = True
    return None

# Strip ANSI escape sequences when extracting submitted commands for filter matching
_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _strip_control(text: str) -> str:
    text = _ANSI_RE.sub("", text)
    return "".join(c for c in text if c.isprintable() or c in ("\t",))


async def _identity_get(path: str, settings, **params) -> Any:
    token = mint("terminal-service", "identity-service", settings.service_jwt_secret)
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(
            f"{settings.identity_service_url}/api/v1{path}",
            headers={"X-Service-Token": token},
            params=params,
        )
    resp.raise_for_status()
    return resp.json()


async def _inventory_get(path: str, settings) -> Any:
    token = mint("terminal-service", "inventory-service", settings.service_jwt_secret)
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(
            f"{settings.inventory_service_url}/api/v1{path}",
            headers={"X-Service-Token": token},
        )
    resp.raise_for_status()
    return resp.json()


async def _post_recording(session_id: str, frames: list[dict], duration: float, settings) -> None:
    token = mint("terminal-service", "recording-service", settings.service_jwt_secret)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{settings.recording_service_url}/api/v1/internal/recordings",
                json={"session_id": session_id, "frames": frames, "duration_seconds": round(duration, 2)},
                headers={"X-Service-Token": token},
            )
    except httpx.HTTPError as exc:
        logger.warning("recording post failed", extra={"session_id": session_id, "error": str(exc)})


_command_log = logging.getLogger("seyalrun.command")  # logger name = routing category


async def _audit_command(session_id: str, command: str, filter_id: str | None, action: str) -> None:
    """Fire-and-forget command audit write — never called in the hot input path."""
    try:
        async with SessionLocal() as db:
            db.add(ZASessionCommand(
                session_id=session_id,
                command_text=command,
                matched_filter_id=filter_id,
                action=action,
            ))
            await db.commit()
    except Exception:
        pass
    # Emit a categorized log line so the shipper can route command logs (e.g. to ES).
    _command_log.info(
        "%s", command,
        extra={"category": "command", "session_id": session_id,
               "command": command, "action": action, "filter_id": filter_id},
    )


async def handle_terminal(websocket: WebSocket, session_id: str, terminate_events: dict[str, asyncio.Event]) -> None:
    """Main entry point — called from main.py's /ws/ssh/{session_id} route."""
    settings = get_settings()

    # Verify service token from WS upgrade headers (api-gateway sets these)
    svc_token = websocket.headers.get("x-service-token", "")
    try:
        verify(svc_token, "terminal-service", settings.service_jwt_secret)
    except (ServiceTokenError, Exception):
        await websocket.close(code=4401)
        return

    user_id = websocket.headers.get("x-user-id", "")
    username = websocket.headers.get("x-user-name", "")
    if not user_id:
        await websocket.close(code=4401)
        return

    await websocket.accept()

    async with SessionLocal() as db:
        result = await db.execute(select(ZASSHSession).where(ZASSHSession.id == session_id))
        sess = result.scalar_one_or_none()
        if sess is None or sess.user_id != user_id:
            await websocket.send_text(json.dumps({"type": "error", "message": "session not found"}))
            await websocket.close(code=4404)
            return
        if sess.status not in ("pending", "active"):
            await websocket.send_text(json.dumps({"type": "error", "message": f"session is {sess.status}"}))
            await websocket.close(code=4400)
            return

        # Fetch host + credential details
        try:
            host = await _inventory_get(f"/hosts/{sess.host_id}", settings)
            cred_secret = await _inventory_get(f"/internal/credentials/{sess.credential_id}/secret", settings)
        except Exception as exc:
            await websocket.send_text(json.dumps({"type": "error", "message": "failed to fetch host/credential"}))
            await websocket.close(code=4500)
            logger.exception("credential fetch failed", extra={"session_id": session_id})
            return

        # Resolve the ProxyJump chain, if this host's zone (or any of its ancestor
        # zones) has a gateway. Each ancestor zone contributes at most one hop, root
        # first, ending with the target host's own zone — "ssh -J gw1,gw2,...".
        tunnel_conn = None
        zone_id = host.get("zone_id")
        if zone_id:
            try:
                chain_data = await _inventory_get(f"/internal/zones/{zone_id}/gateway-chain", settings)
            except Exception:
                chain_data = {"chain": []}
            for hop in chain_data.get("chain", []):
                try:
                    gw_cred_id = hop.get("credential_id")
                    if gw_cred_id:
                        gw_cred_data = await _inventory_get(f"/internal/credentials/{gw_cred_id}/secret", settings)
                        gw_username = gw_cred_data["username"]
                        gw_password = gw_cred_data["secret"].get("password") if gw_cred_data["secret_type"] == "password" else None
                        gw_keys = (
                            [asyncssh.import_private_key(gw_cred_data["secret"]["private_key"])]
                            if gw_cred_data["secret_type"] == "ssh_key"
                            else []
                        )
                    else:
                        gw_username = hop.get("username", "")
                        gw_password = None
                        gw_keys = []
                    tunnel_conn = await asyncssh.connect(
                        hop["host"],
                        port=int(hop.get("port", 22)),
                        username=gw_username,
                        password=gw_password,
                        client_keys=gw_keys,
                        known_hosts=_known_hosts(),
                        tunnel=tunnel_conn,
                    )
                except Exception as exc:
                    await websocket.send_text(json.dumps({"type": "error", "message": f"gateway '{hop.get('zone_name', hop.get('host'))}' connection failed: {exc}"}))
                    await websocket.close(code=4502)
                    return

        # Fetch initial command filters (cached per session, refreshed later).
        # IMPORTANT: always set filters_fetched_at to now(), even on failure.
        # Leaving it at 0.0 means every Enter keypress retries a 5-second
        # network call, making the terminal feel frozen after the first command.
        filters: list[dict] = []
        default_deny: bool = False
        try:
            _fr = await _identity_get("/internal/command-filters", settings, user_id=user_id, host_id=sess.host_id)
            if isinstance(_fr, dict):
                filters = _fr.get("filters", [])
                default_deny = _fr.get("default_deny", False)
            elif isinstance(_fr, list):
                filters = _fr  # backward compat if endpoint ever returns plain list
        except Exception:
            pass
        filters_fetched_at = time.monotonic()

        # Command-filter matchers are process-wide and immutable after startup — grab them
        # ONCE here instead of re-importing app on every filter check in the hot input path.
        from ..main import app as _app
        filter_matchers = getattr(getattr(_app, "state", None), "filter_matchers", {}) or {}

        # Connect via asyncssh
        connect_kwargs: dict[str, Any] = {
            "host": host["ip"],
            "port": int(host.get("port") or 22),
            "username": cred_secret["username"],
            "known_hosts": _known_hosts(),
            "login_timeout": 20,
            "connect_timeout": 15,
            # SSH-level keepalives — prevent the server from dropping idle sessions.
            "keepalive_interval": 30,
            "keepalive_count_max": 5,
        }
        if tunnel_conn:
            connect_kwargs["tunnel"] = tunnel_conn
        secret_type = cred_secret.get("secret_type", "password")
        secret_data = cred_secret.get("secret", {})
        if secret_type == "password":
            connect_kwargs["password"] = secret_data.get("password", "")
        elif secret_type == "ssh_key":
            private_key_str = secret_data.get("private_key", "")
            passphrase = secret_data.get("passphrase") or None
            connect_kwargs["client_keys"] = [asyncssh.import_private_key(private_key_str, passphrase)]

        try:
            ssh_conn = await asyncssh.connect(**connect_kwargs)
        except Exception as exc:
            # Some asyncssh exceptions stringify to "" — fall back to the class name so the
            # reason is never blank, and include the connection context for the UI dialog.
            detail = str(exc).strip() or f"{type(exc).__name__} (no further detail from the SSH server)"
            _addr = host["ip"]
            _port = int(host.get("port") or 22)
            _user = connect_kwargs.get("username", "")
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": f"SSH connection failed: {detail}",
                "detail": detail,
                "user": _user,
                "host": host.get("name") or _addr,
                "address": _addr,
                "port": _port,
            }))
            await websocket.close(code=4502)
            sess.status = "error"
            sess.ended_at = datetime.now(timezone.utc)
            sess.error_message = detail
            await db.commit()
            if tunnel_conn:
                tunnel_conn.close()
            return

        # Clear plaintext credentials from local scope
        del connect_kwargs, secret_data, cred_secret

        sess.status = "active"
        await db.commit()

        # Register terminate event
        terminate_event = asyncio.Event()
        terminate_events[session_id] = terminate_event

        frames: list[dict] = []
        t0 = time.monotonic()
        cmd_buf = ""
        pending_confirm: asyncio.Future[bool] | None = None
        _ws_send_failed = False  # tracks whether the WS (not SSH) is the dead side

        # Read initial terminal dimensions from WS query params (set by the client
        # AFTER fitAddon.fit() so the PTY is created at the correct size from the
        # start — avoids the 80×24 default → SIGWINCH → zsh-redraw cascade that
        # puts the cursor on the wrong row).
        initial_cols = max(20, int(websocket.query_params.get("cols", 80)))
        initial_rows = max(5,  int(websocket.query_params.get("rows", 24)))
        logger.info("pty_create", extra={"session": session_id[:8], "cols": initial_cols, "rows": initial_rows})
        # Track current PTY size so we only call change_terminal_size when dims
        # actually change — identical resize triggers SIGWINCH which causes zsh
        # to redraw the prompt and leave the cursor on the wrong row.
        pty_cols = initial_cols
        pty_rows = initial_rows

        # Pre-initialise so the finally block is safe even if create_process() fails
        # before these are assigned.
        process = None
        ssh_reader_task = None
        terminate_task = None

        try:
            logger.info("create_process starting", extra={"session": session_id[:8],
                        "host": host["ip"], "cols": initial_cols, "rows": initial_rows})
            try:
                process = await asyncio.wait_for(
                    ssh_conn.create_process(
                        term_type="xterm-256color",
                        term_size=(initial_cols, initial_rows),
                        encoding=None,  # bytes mode: avoids line-buffering that hides the shell prompt
                    ),
                    timeout=settings.ssh_shell_timeout,
                )
            except asyncio.TimeoutError:
                logger.error("create_process timed out — SSH server did not reply to shell request",
                             extra={"session": session_id[:8], "host": host["ip"],
                                    "timeout": settings.ssh_shell_timeout})
                sess.status = "error"
                sess.error_message = (
                    f"SSH shell did not start within {settings.ssh_shell_timeout}s "
                    "(server did not reply to PTY/shell request)"
                )
                sess.ended_at = datetime.now(timezone.utc)
                try:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": f"SSH shell did not start within {int(settings.ssh_shell_timeout)}s — "
                                   "the server may be busy or require PTY allocation. Try reconnecting.",
                    }))
                except Exception:
                    pass
                return  # finally block still runs for cleanup

            async def from_ssh() -> None:
                nonlocal _ws_send_failed
                logger.info("from_ssh started", extra={"session": session_id[:8]})
                try:
                    while True:
                        try:
                            chunk = await asyncio.wait_for(process.stdout.read(4096), timeout=0.5)
                        except asyncio.TimeoutError:
                            continue
                        if not chunk:
                            break
                        data = chunk.decode("utf-8", errors="replace")
                        # zsh (Oh My Zsh async plugins) sends SIGWINCH-triggered prompt
                        # redraws during startup.  Each interrupted redraw ends with
                        # \x1b[?2004h\x1b[?2004l\r\r\n — enable then immediately disable
                        # bracketed paste + newline — moving the cursor DOWN so the next
                        # redraw stacks below.  Replacing the trailing \r\n with just \r
                        # keeps the cursor on the same line so every redraw overwrites the
                        # previous one, leaving exactly one visible prompt.
                        data = data.replace(
                            "\x1b[?2004h\x1b[?2004l\r\r\n",
                            "\x1b[?2004h\x1b[?2004l\r",
                        )
                        frames.append({"t": round(time.monotonic() - t0, 3), "d": data})
                        if len(frames) > settings.terminal_recording_max_frames:
                            frames.pop(0)
                        try:
                            await websocket.send_text(json.dumps({"type": "output", "data": data}))
                        except Exception as send_exc:
                            logger.error(
                                "from_ssh: WS send failed — browser connection likely dropped",
                                extra={"session": session_id[:8], "error": type(send_exc).__name__,
                                       "detail": str(send_exc)[:200]},
                            )
                            _ws_send_failed = True
                            return
                except Exception as exc:
                    logger.info(
                        "from_ssh: SSH stream ended",
                        extra={"session": session_id[:8], "exc_type": type(exc).__name__,
                               "detail": str(exc)[:200]},
                    )
                    reason = getattr(exc, "reason", None) or str(exc)
                    if isinstance(exc, (asyncssh.DisconnectError, asyncssh.ConnectionLost)):
                        try:
                            await websocket.send_text(json.dumps({
                                "type": "error",
                                "message": f"SSH disconnected: {reason}",
                            }))
                        except Exception:
                            pass
                logger.info("from_ssh exited", extra={"session": session_id[:8], "ws_send_failed": _ws_send_failed})

            ssh_reader_task = asyncio.create_task(from_ssh())

            async def check_filters() -> tuple[list[dict], bool]:
                nonlocal filters, default_deny, filters_fetched_at
                now = time.monotonic()
                if now - filters_fetched_at > settings.terminal_policy_refresh_seconds:
                    try:
                        resp = await _identity_get(
                            "/internal/command-filters", settings, user_id=user_id, host_id=sess.host_id
                        )
                        if isinstance(resp, dict):
                            filters = resp.get("filters", [])
                            default_deny = resp.get("default_deny", False)
                        elif isinstance(resp, list):
                            filters = resp
                        filters_fetched_at = now
                    except Exception:
                        pass
                return filters, default_deny

            terminate_task = asyncio.create_task(terminate_event.wait())
            last_input = time.monotonic()
            idle_limit = settings.idle_timeout_seconds

            while True:
                ws_recv_task = asyncio.create_task(websocket.receive_text())
                done, pending = await asyncio.wait(
                    [ws_recv_task, ssh_reader_task, terminate_task],
                    return_when=asyncio.FIRST_COMPLETED,
                    timeout=15,
                )

                # Idle timeout — close the session after idle_limit seconds with no client input.
                if not done:
                    if idle_limit and (time.monotonic() - last_input) > idle_limit:
                        try:
                            await websocket.send_text(json.dumps({"type": "output",
                                "data": f"\r\n\x1b[33m── Disconnected after {idle_limit // 60} min of inactivity ──\x1b[0m\r\n"}))
                        except Exception:
                            pass
                        logger.info("session idle-timeout", extra={"session": session_id[:8], "idle_s": idle_limit})
                        for t in pending:
                            t.cancel()
                        break
                    ws_recv_task.cancel()
                    continue

                if terminate_task in done or terminate_event.is_set():
                    for t in pending:
                        t.cancel()
                    break

                if ssh_reader_task in done:
                    # SSH stream ended (either the remote shell exited, or the WS send
                    # failed and we marked _ws_send_failed).  Either way, the session
                    # is over — break out and let the finally block clean up.
                    for t in pending:
                        t.cancel()
                    break

                if ws_recv_task not in done:
                    for t in pending:
                        t.cancel()
                    break

                try:
                    raw = ws_recv_task.result()
                except Exception:
                    break

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type")

                if msg_type == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))

                elif msg_type == "resize":
                    new_rows = int(msg.get("rows", 24))
                    new_cols = int(msg.get("cols", 80))
                    changed = new_cols != pty_cols or new_rows != pty_rows
                    logger.info(
                        "resize",
                        extra={"session": session_id[:8], "new": f"{new_cols}x{new_rows}",
                               "pty": f"{pty_cols}x{pty_rows}", "changed": changed},
                    )
                    if changed:
                        try:
                            process.change_terminal_size(new_cols, new_rows)
                            pty_cols = new_cols
                            pty_rows = new_rows
                        except Exception:
                            pass

                elif msg_type == "confirm":
                    if pending_confirm and not pending_confirm.done():
                        pending_confirm.set_result(bool(msg.get("allow", False)))

                elif msg_type == "input":
                    data: str = msg.get("data", "")
                    if not data:
                        continue
                    last_input = time.monotonic()   # reset idle timer on real client input

                    # Accumulate into command buffer; flush + filter-check on newline
                    cmd_buf += data
                    if "\r" in data or "\n" in data:
                        command = _strip_control(cmd_buf).strip()
                        cmd_buf = ""

                        matched_filter: dict | None = None
                        current_filters, current_default_deny = await check_filters()
                        for f in current_filters:
                            match_type = f.get("match_type", "regex")
                            patterns: list[str] = f.get("patterns", [])
                            matcher = filter_matchers.get(match_type)  # grabbed once at session start
                            if matcher and matcher.matches(command, patterns):
                                matched_filter = f
                                break

                        if matched_filter:
                            # Default to "deny" when action is missing — never silently allow.
                            action = matched_filter.get("action", "deny")
                            filter_name = matched_filter.get("name", "")
                            filter_id = matched_filter.get("id", "")

                            # Audit non-blocking — never stall the input path for a DB commit
                            asyncio.create_task(_audit_command(
                                session_id, command, filter_id or None, action
                            ))

                            if action == "deny":
                                await _clear_remote_line(process)
                                await websocket.send_text(json.dumps({
                                    "type": "output",
                                    "data": f"\r\n[blocked by policy: {filter_name}]\r\n",
                                }))
                                continue

                            if action == "confirm":
                                pending_confirm = asyncio.get_running_loop().create_future()
                                await websocket.send_text(json.dumps({
                                    "type": "confirm_required",
                                    "command": command,
                                    "filter": filter_name,
                                    "filter_id": filter_id,
                                }))
                                try:
                                    allowed = await asyncio.wait_for(pending_confirm, timeout=30.0)
                                except asyncio.TimeoutError:
                                    allowed = False
                                pending_confirm = None
                                if not allowed:
                                    await _clear_remote_line(process)
                                    await websocket.send_text(json.dumps({
                                        "type": "output",
                                        "data": "\r\n[blocked by user decision]\r\n",
                                    }))
                                    continue
                        else:
                            if command:
                                if current_default_deny:
                                    # No filter matched but default-deny policy is active — block.
                                    await _clear_remote_line(process)
                                    asyncio.create_task(_audit_command(session_id, command, None, "denied_by_default"))
                                    await websocket.send_text(json.dumps({
                                        "type": "output",
                                        "data": "\r\n[blocked by default policy]\r\n",
                                    }))
                                    continue
                                asyncio.create_task(_audit_command(session_id, command, None, "logged"))

                        # Forward input to SSH (bytes mode — encode and drain so
                        # each keystroke is flushed immediately without buffering)
                        try:
                            process.stdin.write(data.encode("utf-8"))
                            await process.stdin.drain()
                        except Exception:
                            break

                    else:
                        # Not a newline — forward immediately (character-by-character navigation)
                        try:
                            process.stdin.write(data.encode("utf-8"))
                            await process.stdin.drain()
                        except Exception:
                            break

        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.exception("terminal handler error", extra={"session_id": session_id})
        finally:
            if ssh_reader_task is not None:
                ssh_reader_task.cancel()
            if terminate_task is not None:
                terminate_task.cancel()
            if process is not None:
                try:
                    process.close()
                except Exception:
                    pass
            ssh_conn.close()
            if tunnel_conn:
                tunnel_conn.close()
            # Explicitly close the WS so the client receives a proper close frame
            # and can display "Disconnected" + a reconnect prompt immediately.
            try:
                await websocket.close(code=1000)
            except Exception:
                pass

            terminate_events.pop(session_id, None)

            duration = time.monotonic() - t0
            # Preserve "error" status set by earlier handlers (e.g. shell timeout).
            if sess.status not in ("error", "terminated"):
                sess.status = "closed" if not terminate_event.is_set() else "terminated"
            if not sess.ended_at:
                sess.ended_at = datetime.now(timezone.utc)
            await db.commit()

            # Post recording to recording-service (best-effort)
            if frames:
                asyncio.create_task(_post_recording(session_id, frames, duration, settings))
