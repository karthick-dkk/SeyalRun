"""SFTP file management — Increment 1 of the terminal parity plan.

A separate module with the same shape as api/sessions.py, mounted alongside it,
so the file-transfer surface is reviewable on its own rather than buried in the
terminal WebSocket. It rides the SSH connection that session already opened
(see app/sftp_registry.py): one authentication, one credential unwrap, one
ProxyJump chain.

Three things matter more here than anywhere else in the service, because this is
the only path by which bytes leave or enter a managed host:

1. **The grants become real.** AuthorizationsAdmin has offered `sftp`, `upload`
   and `download` as grantable actions since before any of them was implemented
   — stored, audited, enforcing nothing (R-11). An access review would report
   "user X may download from host Y" while no file transfer existed at all. Every
   operation below resolves the SAME za_authorization record SSH uses and checks
   the specific action. `sftp` alone browses; it does not download.

2. **Every operation is audited with a result**, per R-10 — including the
   refusals, which are the rows an assessor actually looks for.

3. **Transfers are `critical=True`**: a download that cannot be written to the
   audit chain does not happen. "We handed over the file but have no record"
   is the exact situation PCI DSS Req 10 exists to prevent, and it is why the
   audit call precedes the byte stream rather than following it.

Path handling: every path is resolved against the SFTP server's realpath and
rejected if it escapes, so `../../etc/shadow` cannot be reached by a caller who
was only ever meant to browse.
"""

from __future__ import annotations

import logging
import posixpath
import stat as statmod
from typing import Any

import httpx
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.elevation import elevation_active
from libs.servicetoken import mint

from .. import sftp_registry
from ..audit import log_action
from ..config import get_settings
from ..database import get_session
from ..deps import current_user_id, current_username, current_user_role, require_service_token
from ..models import ZASSHSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sftp", tags=["sftp"], dependencies=[Depends(require_service_token)])

# Transfers are streamed in chunks; this bounds memory per request, not file size.
CHUNK = 64 * 1024

# Where the file manager opens, on every host. /tmp rather than the account's
# home directory: it exists on every managed server, every account can read it,
# and it is the conventional drop point for the transfers this feature is for —
# so the panel opens somewhere useful instead of somewhere that may not exist.
# Callers may navigate anywhere the account can reach; this is only the default.
DEFAULT_PATH = "/tmp"

# Transfer ceiling, both directions. A PAM is not a file-sharing service, and an
# unbounded upload is a trivial way to fill a managed host's disk from a session
# that was only granted "put a config file there". Applied to downloads too so
# "max file size" means one number rather than two.
MAX_TRANSFER_BYTES = 1024 * 1024 * 1024   # 1 GiB


class _Entry(BaseModel):
    name: str
    path: str
    is_dir: bool
    size: int
    mtime: float
    mode: str


class ListOut(BaseModel):
    path: str
    entries: list[_Entry]


class PathIn(BaseModel):
    path: str


class RenameIn(BaseModel):
    path: str
    new_path: str


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


async def _authorize(
    session_id: str,
    action: str,
    db: AsyncSession,
    user_id: str,
    username: str,
    role: str,
    elevated_until: str,
) -> tuple[ZASSHSession, Any, str]:
    """Resolve the session, confirm ownership, and check `action` against the same
    za_authorization record SSH used — then return the live SSH connection.

    Ordering is deliberate: a refusal is audited BEFORE the HTTPException is
    raised, because a denied file transfer is precisely the event worth keeping.
    Every failure path here writes a row with result="failure".
    """
    settings = get_settings()

    sess = (await db.execute(select(ZASSHSession).where(ZASSHSession.id == session_id))).scalar_one_or_none()
    if sess is None or sess.user_id != user_id:
        # Not audited against a host — there is no established host to name, and
        # an unauthenticated probe for session ids must not write chain rows.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")

    async def _deny(detail: str) -> HTTPException:
        await log_action(
            user_id=user_id, username=username, action=f"sftp.{action}",
            resource_type="host", resource_id=sess.host_id, session_id=session_id,
            details={"reason": detail, "host_name": sess.host_name}, result="failure",
        )
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)

    if sess.status != "active":
        # Previously raised without a row. "All SFTP operations are audited" has to
        # include the ones that never reached the filesystem, or the log answers
        # "what happened" only for the attempts that got far enough to succeed.
        await log_action(
            user_id=user_id, username=username, action=f"sftp.{action}",
            resource_type="host", resource_id=sess.host_id, session_id=session_id,
            details={"reason": f"session is {sess.status}", "host_name": sess.host_name},
            result="failure",
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"session is {sess.status}")

    # Same resolve call, same record, same semantics as sessions.py step 3 — an
    # empty actions list means "no per-action restriction", anything else must
    # name this action explicitly.
    authz = await _identity_get("/internal/authz/resolve", settings, user_id=user_id, host_id=sess.host_id)
    actions: list[str] = authz.get("actions", [])
    elevated = role in ("admin", "superadmin") and elevation_active(elevated_until)
    if actions and action not in actions and not elevated:
        raise await _deny(f"{action} action not permitted for this host")

    conn = sftp_registry.get(session_id)
    if conn is None:
        await log_action(
            user_id=user_id, username=username, action=f"sftp.{action}",
            resource_type="host", resource_id=sess.host_id, session_id=session_id,
            details={"reason": "no live SSH connection", "host_name": sess.host_name},
            result="failure",
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="no live SSH connection for this session — reconnect the terminal first",
        )
    return sess, conn, settings.sftp_root


class SftpPathDenied(Exception):
    """Raised when a resolved path lies outside the configured SFTP root."""


def _within_root(resolved: str, root: str) -> bool:
    """Containment test on an ALREADY-RESOLVED path.

    Compared component-wise rather than with startswith(), which would accept
    "/tmpevil" as being inside "/tmp".
    """
    root = posixpath.normpath(root)
    if root == "/":
        return True
    resolved = posixpath.normpath(resolved)
    return resolved == root or resolved.startswith(root.rstrip("/") + "/")


async def _resolve(sftp, path: str, root: str) -> str:
    """Resolve `path` through the SERVER's realpath, then confine it to `root`.

    Order is the whole point, and the first version of this function got it
    wrong: it normalised the string and returned it, rejecting nothing at all.
    An absolute path passed straight through, so a caller granted `download`
    could read /etc/shadow, any private key, any application secret that account
    could read — through what looks in the grant like "may fetch files". The
    runtime proof for this feature downloaded /etc/hostname without noticing.

    Two reasons resolution must happen server-side and first:

      * `..` is only meaningful after normalisation, and normalising locally
        cannot see what the remote path actually is;
      * realpath resolves SYMLINKS. Checking the string before resolution would
        accept /tmp/escape when /tmp/escape -> /etc, which is the obvious way
        around a naive prefix check — and a link an ordinary user can create.
    """
    target = path if path.startswith("/") else posixpath.join(root, path)
    try:
        resolved = await sftp.realpath(target)
    except Exception:
        # Non-existent leaf (an upload target, a mkdir) — resolve the parent,
        # which must exist, and re-attach the basename.
        parent = posixpath.dirname(posixpath.normpath(target)) or "/"
        base = posixpath.basename(posixpath.normpath(target))
        resolved_parent = await sftp.realpath(parent)
        resolved = posixpath.join(resolved_parent, base) if base else resolved_parent
    if not _within_root(resolved, root):
        raise SftpPathDenied(resolved)
    return resolved


def _mode_str(mode: int) -> str:
    return statmod.filemode(mode) if mode else ""


async def _deny_path(action: str, sess, session_id: str, user_id: str, username: str, resolved: str, root: str) -> HTTPException:
    """A path escape is an access-control event, not a bad request — audited as a
    refusal and reported as 403, with the root named so the operator can see the
    boundary rather than guess at it."""
    await log_action(
        user_id=user_id, username=username, action=f"sftp.{action}", resource_type="host",
        resource_id=sess.host_id, session_id=session_id,
        details={"path": resolved, "reason": "outside sftp root", "sftp_root": root,
                 "host_name": sess.host_name},
        result="failure", critical=True,
    )
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"path is outside the permitted SFTP root ({root})",
    )


@router.get("/{session_id}/list", response_model=ListOut)
async def list_dir(
    session_id: str,
    path: str = DEFAULT_PATH,
    db: AsyncSession = Depends(get_session),
    user_id: str = Depends(current_user_id),
    username: str = Depends(current_username),
    role: str = Depends(current_user_role),
    elevated_until: str = Header(default="", alias="X-Elevated-Until"),
):
    sess, conn, root = await _authorize(session_id, "sftp", db, user_id, username, role, elevated_until)
    try:
        async with conn.start_sftp_client() as sftp:
            cwd = await _resolve(sftp, path, root)
            names = await sftp.listdir(cwd)
            entries: list[_Entry] = []
            for name in sorted(names):
                if name in (".", ".."):
                    continue
                full = posixpath.join(cwd, name)
                try:
                    attrs = await sftp.stat(full)
                except Exception:
                    continue          # broken symlink or races with a delete
                entries.append(_Entry(
                    name=name, path=full,
                    is_dir=statmod.S_ISDIR(attrs.permissions or 0),
                    size=attrs.size or 0, mtime=float(attrs.mtime or 0),
                    mode=_mode_str(attrs.permissions or 0),
                ))
    except SftpPathDenied as denied:
        # Must precede the broad handler below, or a containment refusal would be
        # reported as "cannot list" — a 400 that reads like a bad path rather than
        # the access-control decision it is.
        raise await _deny_path("list", sess, session_id, user_id, username, str(denied), root)
    except HTTPException:
        raise
    except Exception as exc:
        await log_action(
            user_id=user_id, username=username, action="sftp.list", resource_type="host",
            resource_id=sess.host_id, session_id=session_id,
            details={"path": path, "error": str(exc)[:200]}, result="failure",
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"cannot list '{path}': {exc}") from exc

    await log_action(
        user_id=user_id, username=username, action="sftp.list", resource_type="host",
        resource_id=sess.host_id, session_id=session_id,
        details={"path": cwd, "entries": len(entries), "host_name": sess.host_name}, result="success",
    )
    return ListOut(path=cwd, entries=entries)


@router.get("/{session_id}/download")
async def download(
    session_id: str,
    path: str,
    db: AsyncSession = Depends(get_session),
    user_id: str = Depends(current_user_id),
    username: str = Depends(current_username),
    role: str = Depends(current_user_role),
    elevated_until: str = Header(default="", alias="X-Elevated-Until"),
):
    """Requires the `download` action specifically. A grant of `sftp` alone
    browses and does not download — that distinction is the whole reason the
    three actions exist separately, and it is what R-11 promised and never
    delivered."""
    sess, conn, root = await _authorize(session_id, "download", db, user_id, username, role, elevated_until)

    async with conn.start_sftp_client() as sftp:
        try:
            target = await _resolve(sftp, path, root)
        except SftpPathDenied as denied:
            raise await _deny_path("download", sess, session_id, user_id, username, str(denied), root)
        try:
            attrs = await sftp.stat(target)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no such file: {path}") from exc
        if statmod.S_ISDIR(attrs.permissions or 0):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cannot download a directory")
        size = attrs.size or 0
        if size > MAX_TRANSFER_BYTES:
            # Refused before the audit row, deliberately: nothing left the host, so
            # recording it as a completed transfer would overstate what happened.
            # The attempt is still logged, as a failure.
            await log_action(
                user_id=user_id, username=username, action="sftp.download", resource_type="host",
                resource_id=sess.host_id, session_id=session_id,
                details={"path": target, "bytes": size, "reason": "exceeds transfer limit"},
                result="failure",
            )
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"file is {size // (1024 ** 2)} MiB — exceeds the {MAX_TRANSFER_BYTES // (1024 ** 3)} GiB transfer limit",
            )

    # critical=True, and BEFORE a single byte moves: if this row cannot be
    # written, the transfer must not happen. Auditing after the stream would
    # record only the transfers that happened to succeed.
    await log_action(
        user_id=user_id, username=username, action="sftp.download", resource_type="host",
        resource_id=sess.host_id, session_id=session_id,
        details={"path": target, "bytes": size, "host_name": sess.host_name},
        result="success", critical=True,
    )

    async def _stream():
        async with conn.start_sftp_client() as sftp:
            async with sftp.open(target, "rb") as fh:
                while True:
                    chunk = await fh.read(CHUNK)
                    if not chunk:
                        break
                    yield chunk

    filename = posixpath.basename(target) or "download"
    return StreamingResponse(
        _stream(),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(size),
        },
    )


@router.post("/{session_id}/upload", status_code=status.HTTP_201_CREATED)
async def upload(
    session_id: str,
    path: str = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_session),
    user_id: str = Depends(current_user_id),
    username: str = Depends(current_username),
    role: str = Depends(current_user_role),
    elevated_until: str = Header(default="", alias="X-Elevated-Until"),
):
    """Requires the `upload` action specifically."""
    sess, conn, root = await _authorize(session_id, "upload", db, user_id, username, role, elevated_until)

    written = 0
    async with conn.start_sftp_client() as sftp:
        try:
            base = await _resolve(sftp, path, root)
        # Re-resolve the final target: basename() alone would not stop an upload
        # into a symlinked subdirectory pointing out of the root.
            target = await _resolve(sftp, posixpath.join(base, posixpath.basename(file.filename or "upload")), root)
        except SftpPathDenied as denied:
            raise await _deny_path("upload", sess, session_id, user_id, username, str(denied), root)
        try:
            async with sftp.open(target, "wb") as fh:
                while True:
                    chunk = await file.read(CHUNK)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_TRANSFER_BYTES:
                        await log_action(
                            user_id=user_id, username=username, action="sftp.upload",
                            resource_type="host", resource_id=sess.host_id, session_id=session_id,
                            details={"path": target, "reason": "exceeds upload limit"}, result="failure",
                        )
                        raise HTTPException(
                            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail=f"upload exceeds the {MAX_TRANSFER_BYTES // (1024 ** 3)} GiB limit",
                        )
                    await fh.write(chunk)
        except HTTPException:
            raise
        except Exception as exc:
            await log_action(
                user_id=user_id, username=username, action="sftp.upload", resource_type="host",
                resource_id=sess.host_id, session_id=session_id,
                details={"path": target, "error": str(exc)[:200]}, result="failure",
            )
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"upload failed: {exc}") from exc

    await log_action(
        user_id=user_id, username=username, action="sftp.upload", resource_type="host",
        resource_id=sess.host_id, session_id=session_id,
        details={"path": target, "bytes": written, "host_name": sess.host_name},
        result="success", critical=True,
    )
    return {"path": target, "bytes": written}


@router.post("/{session_id}/mkdir", status_code=status.HTTP_201_CREATED)
async def mkdir(
    session_id: str,
    payload: PathIn,
    db: AsyncSession = Depends(get_session),
    user_id: str = Depends(current_user_id),
    username: str = Depends(current_username),
    role: str = Depends(current_user_role),
    elevated_until: str = Header(default="", alias="X-Elevated-Until"),
):
    sess, conn, root = await _authorize(session_id, "sftp", db, user_id, username, role, elevated_until)
    async with conn.start_sftp_client() as sftp:
        try:
            target = await _resolve(sftp, payload.path, root)
        except SftpPathDenied as denied:
            raise await _deny_path("mkdir", sess, session_id, user_id, username, str(denied), root)
        try:
            await sftp.mkdir(target)
        except Exception as exc:
            await log_action(
                user_id=user_id, username=username, action="sftp.mkdir", resource_type="host",
                resource_id=sess.host_id, session_id=session_id,
                details={"path": target, "error": str(exc)[:200]}, result="failure",
            )
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"mkdir failed: {exc}") from exc
    await log_action(
        user_id=user_id, username=username, action="sftp.mkdir", resource_type="host",
        resource_id=sess.host_id, session_id=session_id,
        details={"path": target, "host_name": sess.host_name}, result="success",
    )
    return {"path": target}


@router.post("/{session_id}/rename")
async def rename(
    session_id: str,
    payload: RenameIn,
    db: AsyncSession = Depends(get_session),
    user_id: str = Depends(current_user_id),
    username: str = Depends(current_username),
    role: str = Depends(current_user_role),
    elevated_until: str = Header(default="", alias="X-Elevated-Until"),
):
    sess, conn, root = await _authorize(session_id, "sftp", db, user_id, username, role, elevated_until)
    async with conn.start_sftp_client() as sftp:
        try:
            src = await _resolve(sftp, payload.path, root)
            dst = await _resolve(sftp, payload.new_path, root)
        except SftpPathDenied as denied:
            raise await _deny_path("rename", sess, session_id, user_id, username, str(denied), root)
        try:
            await sftp.rename(src, dst)
        except Exception as exc:
            await log_action(
                user_id=user_id, username=username, action="sftp.rename", resource_type="host",
                resource_id=sess.host_id, session_id=session_id,
                details={"from": src, "to": dst, "error": str(exc)[:200]}, result="failure",
            )
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"rename failed: {exc}") from exc
    await log_action(
        user_id=user_id, username=username, action="sftp.rename", resource_type="host",
        resource_id=sess.host_id, session_id=session_id,
        details={"from": src, "to": dst, "host_name": sess.host_name}, result="success",
    )
    return {"path": dst}


@router.delete("/{session_id}/rm")
async def remove(
    session_id: str,
    path: str,
    is_dir: bool = False,
    db: AsyncSession = Depends(get_session),
    user_id: str = Depends(current_user_id),
    username: str = Depends(current_username),
    role: str = Depends(current_user_role),
    elevated_until: str = Header(default="", alias="X-Elevated-Until"),
):
    sess, conn, root = await _authorize(session_id, "sftp", db, user_id, username, role, elevated_until)
    async with conn.start_sftp_client() as sftp:
        try:
            target = await _resolve(sftp, path, root)
        except SftpPathDenied as denied:
            raise await _deny_path("delete", sess, session_id, user_id, username, str(denied), root)
        try:
            await (sftp.rmdir(target) if is_dir else sftp.remove(target))
        except Exception as exc:
            await log_action(
                user_id=user_id, username=username, action="sftp.delete", resource_type="host",
                resource_id=sess.host_id, session_id=session_id,
                details={"path": target, "error": str(exc)[:200]}, result="failure",
            )
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"delete failed: {exc}") from exc
    await log_action(
        user_id=user_id, username=username, action="sftp.delete", resource_type="host",
        resource_id=sess.host_id, session_id=session_id,
        details={"path": target, "is_dir": is_dir, "host_name": sess.host_name},
        result="success", critical=True,
    )
    return {"path": target}
