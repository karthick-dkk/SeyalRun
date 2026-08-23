from __future__ import annotations

import jwt
import pydantic
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.elevation import elevation_active
from libs.pluginbase import discover_plugins, CredentialKind

from .. import audit
from ..config import get_settings
from ..database import get_session
from ..deps import require_admin, require_service_token, service_token_claims
from ..models import (
    ZACredential,
    ZACredentialHistory,
    ZACredentialHostLink,
    ZACredentialTemplate,
)
from ..schemas import (
    CredentialCreate,
    CredentialOut,
    CredentialSecretOut,
    CredentialTemplateCreate,
    CredentialTemplateOut,
)
from ..vault import VaultDecryptError, decrypt, decrypt_envelope, encrypt_envelope

# How many prior secrets to retain per credential. Two gives you the previous
# password for "what was it before this rotation" and one behind it for a
# botched rotation, without accumulating an unbounded archive of still-valid
# credentials.
SECRET_HISTORY_KEEP = 2

router = APIRouter(tags=["credentials"], dependencies=[Depends(require_service_token)])

_credential_kinds: dict[str, CredentialKind] = discover_plugins("app.plugins.credentials", CredentialKind)


def _kind(secret_type: str) -> CredentialKind:
    kind = _credential_kinds.get(secret_type)
    if kind is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"unknown secret_type '{secret_type}'")
    return kind


def _encrypt_secret(plaintext: str) -> tuple[str, str]:
    """PCI DSS Phase C: every new write goes through the envelope scheme —
    returns (ciphertext, wrapped_dek), both must be stored together."""
    return encrypt_envelope(plaintext)


def _decrypt_secret(cred: ZACredential) -> str:
    """wrapped_dek is NULL on rows written before Phase C shipped — fall back to
    the old single-KEK decrypt() for those; every row gets migrated to the
    envelope scheme automatically the next time it's written (update/rotate)."""
    if cred.wrapped_dek:
        return decrypt_envelope(cred.secret_ciphertext, cred.wrapped_dek)
    return decrypt(cred.secret_ciphertext)


def _strength_score(secret_type: str, secret: dict) -> int | None:
    """zxcvbn score 0-4 for password credentials; None for keys/vault paths (Feature 9)."""
    if secret_type != "password":
        return None
    pwd = secret.get("password") or ""
    if not pwd:
        return None
    try:
        from zxcvbn import zxcvbn
        return int(zxcvbn(pwd)["score"])
    except Exception:
        return None


async def _credential_out(session: AsyncSession, cred: ZACredential) -> CredentialOut:
    result = await session.execute(select(ZACredentialHostLink.host_id).where(ZACredentialHostLink.credential_id == cred.id))
    host_ids = [h for (h,) in result.all()]
    return CredentialOut(
        id=cred.id,
        name=cred.name,
        template_id=cred.template_id,
        username=cred.username,
        secret_type=cred.secret_type,
        credential_scope=cred.credential_scope,
        is_default=cred.is_default,
        is_sudo=cred.is_sudo,
        is_push_account=cred.is_push_account,
        strength_score=cred.strength_score,
        last_rotated_at=cred.last_rotated_at,
        created_at=cred.created_at,
        updated_at=cred.updated_at,
        host_ids=host_ids,
    )


# ── Credential Templates (Account Templates) ────────────────────────────────

@router.get("/credential-templates", response_model=list[CredentialTemplateOut])
async def list_credential_templates(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZACredentialTemplate))
    return result.scalars().all()


def _encrypt_template_secret(template: ZACredentialTemplate, secret: dict) -> None:
    """Store a template secret under the SAME envelope scheme as ZACredential —
    per-row DEK, KEK-wrapped (app/vault.encrypt_envelope).

    Deliberately not a second scheme: another encryption story is another thing
    ops/rotate_vault_key.py can miss, and missing one is precisely how R-6
    happened. That script rotates za_credential_templates as of the same commit
    that added these columns.
    """
    kind = _kind(template.secret_type)
    try:
        kind.validate(secret)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    template.secret_ciphertext, template.wrapped_dek = _encrypt_secret(kind.encode(secret))


def _decrypt_template_secret(template: ZACredentialTemplate) -> str:
    """Mirrors _decrypt_secret's fallback: wrapped_dek is NULL on any row written
    before the envelope scheme reached this table."""
    if template.wrapped_dek:
        return decrypt_envelope(template.secret_ciphertext, template.wrapped_dek)
    return decrypt(template.secret_ciphertext)


@router.post("/credential-templates", response_model=CredentialTemplateOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def create_credential_template(
    payload: CredentialTemplateCreate,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    existing = await session.execute(select(ZACredentialTemplate).where(ZACredentialTemplate.name == payload.name))
    if existing.scalar_one_or_none():
        await audit.log_action(
            user_id=actor_id, username=actor_name or "", action="credential_template.create",
            resource_type="credential_template", resource_id="",
            details={"name": payload.name, "reason": "name already exists"}, result="failure",
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="template name already exists")
    fields = payload.model_dump(exclude={"secret"})
    template = ZACredentialTemplate(**fields)
    if payload.secret:
        _encrypt_template_secret(template, payload.secret)
    session.add(template)
    await session.commit()
    await session.refresh(template)
    await audit.log_action(
        user_id=actor_id, username=actor_name or "", action="credential_template.create",
        resource_type="credential_template", resource_id=template.id,
        details={"name": template.name, "secret_type": template.secret_type,
                 "has_secret": template.has_secret},
        result="success",
    )
    return template


@router.put("/credential-templates/{template_id}", response_model=CredentialTemplateOut, dependencies=[Depends(require_admin)])
async def update_credential_template(
    template_id: str,
    payload: CredentialTemplateCreate,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    result = await session.execute(select(ZACredentialTemplate).where(ZACredentialTemplate.id == template_id))
    template = result.scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found")
    # exclude secret: an empty dict means "leave the stored secret alone", matching
    # the credential PUT. Blanket setattr over model_dump() would otherwise write
    # `secret` onto the ORM object as a stray attribute and never touch the
    # ciphertext at all — the original "why doesn't it save my password?" bug.
    for field, value in payload.model_dump(exclude={"secret"}).items():
        setattr(template, field, value)
    if payload.secret:
        _encrypt_template_secret(template, payload.secret)
    await session.commit()
    await session.refresh(template)
    await audit.log_action(
        user_id=actor_id, username=actor_name or "", action="credential_template.update",
        resource_type="credential_template", resource_id=template.id,
        details={"name": template.name, "secret_replaced": bool(payload.secret)},
        result="success",
    )
    return template


@router.get("/credential-templates/{template_id}/reveal", response_model=CredentialSecretOut, dependencies=[Depends(require_admin)])
async def reveal_credential_template(
    template_id: str,
    session: AsyncSession = Depends(get_session),
    reveal_token: str = Header("", alias="X-Reveal-Token"),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
    actor_role: str = Header(default="user", alias="X-User-Role"),
    elevated_until: str = Header(default="", alias="X-Elevated-Until"),
):
    """Reveal a template's stored secret — the same MFA + elevation gate as
    /credentials/{id}/reveal, not the plain admin check.

    A template secret is seed material for every account made from it, so reading
    one is at least as sensitive as reading a single account's. The reveal token is
    bound to this template id and this caller, so it cannot be replayed for another
    template or by another user.

    There is no za_authorization grant model for templates (grants are per
    credential), so authorization here is admin/superadmin WITH an active JIT
    elevation — the same fallback branch the credential reveal uses, minus the
    grant path that has nothing to match against.
    """
    settings = get_settings()
    if not reveal_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="reveal token required")
    try:
        claims = jwt.decode(reveal_token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or expired reveal token") from exc
    if claims.get("purpose") != "credential_reveal":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="wrong token purpose")
    if claims.get("cid") != template_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="reveal token not valid for this template")
    if actor_id and claims.get("sub") and claims["sub"] != actor_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="reveal token issued for a different user")

    # Fail closed — a missing actor must never skip the elevation check below.
    if not actor_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user identity required")

    result = await session.execute(select(ZACredentialTemplate).where(ZACredentialTemplate.id == template_id))
    template = result.scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found")
    if not template.secret_ciphertext:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template has no stored secret")

    if not (actor_role in ("admin", "superadmin") and elevation_active(elevated_until)):
        await audit.log_action(
            user_id=actor_id, username=actor_name or "", action="credential_template.viewed",
            resource_type="credential_template", resource_id=template_id,
            details={"name": template.name, "reason": "elevation required"}, result="failure",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="revealing a template secret requires an active elevation",
        )

    try:
        secret = _kind(template.secret_type).decode(_decrypt_template_secret(template))
    except VaultDecryptError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    await audit.log_action(
        user_id=actor_id, username=actor_name or "", action="credential_template.viewed",
        resource_type="credential_template", resource_id=template.id,
        details={"event_type": "elevated_reveal", "name": template.name, "elevation_used": True},
        result="success", critical=True,
    )
    return CredentialSecretOut(
        id=template.id, username=template.default_username,
        secret_type=template.secret_type, secret=secret,
    )


@router.delete("/credential-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)])
async def delete_credential_template(
    template_id: str,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    result = await session.execute(select(ZACredentialTemplate).where(ZACredentialTemplate.id == template_id))
    template = result.scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found")
    name, had_secret = template.name, template.has_secret
    await session.delete(template)
    await session.commit()
    # Accounts already created from this template keep working: the copy is an
    # independent row under its own DEK, and za_credentials.template_id is
    # ON DELETE SET NULL.
    await audit.log_action(
        user_id=actor_id, username=actor_name or "", action="credential_template.delete",
        resource_type="credential_template", resource_id=template_id,
        details={"name": name, "had_secret": had_secret}, result="success",
    )


# ── Credentials ───────────────────────────────────────────────────────────────

@router.get("/credentials", response_model=list[CredentialOut])
async def list_credentials(host_id: str | None = None, session: AsyncSession = Depends(get_session)):
    if host_id:
        stmt = (
            select(ZACredential)
            .join(ZACredentialHostLink, ZACredential.id == ZACredentialHostLink.credential_id)
            .where(ZACredentialHostLink.host_id == host_id)
            .order_by(ZACredential.is_default.desc())
        )
    else:
        stmt = select(ZACredential).order_by(ZACredential.is_default.desc())
    result = await session.execute(stmt)
    return [await _credential_out(session, c) for c in result.scalars().all()]


@router.post("/credentials", response_model=CredentialOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def create_credential(
    payload: CredentialCreate,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    # Create-from-template: no secret supplied but a template that has one. JumpServer
    # PAM's model, and the one the spec settled on — the template is SEED material, so
    # the secret is COPIED into this account and re-encrypted under the account's own
    # fresh DEK. The template's ciphertext is never shared or referenced afterwards,
    # which is what keeps one asset's compromise from handing over every asset seeded
    # from the same template. An explicitly supplied secret always wins.
    secret = payload.secret
    copied_from_template: ZACredentialTemplate | None = None
    if not secret and payload.template_id:
        tmpl = (await session.execute(
            select(ZACredentialTemplate).where(ZACredentialTemplate.id == payload.template_id)
        )).scalar_one_or_none()
        if tmpl is not None and tmpl.secret_ciphertext:
            if tmpl.secret_type != payload.secret_type:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"template holds a {tmpl.secret_type} secret, but this account is {payload.secret_type}",
                )
            try:
                secret = _kind(tmpl.secret_type).decode(_decrypt_template_secret(tmpl))
            except VaultDecryptError as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
            copied_from_template = tmpl

    kind = _kind(payload.secret_type)
    try:
        kind.validate(secret)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # Reading a template secret in order to copy it IS a secret access, so it is
    # audited as one — critical=True, mirroring credential.secret_issued: a copy
    # that cannot be logged must not happen at all.
    if copied_from_template is not None:
        await audit.log_action(
            user_id=actor_id, username=actor_name or "", action="credential.template_secret_used",
            resource_type="credential_template", resource_id=copied_from_template.id,
            details={"template_name": copied_from_template.name, "for_username": payload.username},
            result="success", critical=True,
        )

    ciphertext, wrapped_dek = _encrypt_secret(kind.encode(secret))

    cred = ZACredential(
        name=payload.name,
        template_id=payload.template_id,
        username=payload.username,
        secret_type=payload.secret_type,
        secret_ciphertext=ciphertext,
        wrapped_dek=wrapped_dek,
        credential_scope=payload.credential_scope,
        is_default=payload.is_default,
        is_sudo=payload.is_sudo,
        is_push_account=payload.is_push_account,
        strength_score=_strength_score(payload.secret_type, secret),
    )
    session.add(cred)
    await session.flush()

    for host_id in payload.host_ids:
        session.add(ZACredentialHostLink(credential_id=cred.id, host_id=host_id))

    await session.commit()
    await session.refresh(cred)

    await audit.log_action(
        user_id=actor_id, username=actor_name or "", action="credential.create",
        resource_type="credential", resource_id=cred.id,
        details={"name": cred.name, "secret_type": cred.secret_type, "username": cred.username},
        result="success",
    )
    return await _credential_out(session, cred)


@router.put("/credentials/{credential_id}", response_model=CredentialOut, dependencies=[Depends(require_admin)])
async def update_credential(
    credential_id: str,
    payload: CredentialCreate,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    result = await session.execute(select(ZACredential).where(ZACredential.id == credential_id))
    cred = result.scalar_one_or_none()
    if cred is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="credential not found")

    cred.name = payload.name
    cred.template_id = payload.template_id
    cred.username = payload.username
    cred.secret_type = payload.secret_type
    cred.credential_scope = payload.credential_scope
    cred.is_default = payload.is_default
    cred.is_sudo = payload.is_sudo
    cred.is_push_account = payload.is_push_account

    if payload.secret:  # empty dict = keep existing ciphertext
        kind = _kind(payload.secret_type)
        try:
            kind.validate(payload.secret)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        cred.secret_ciphertext, cred.wrapped_dek = _encrypt_secret(kind.encode(payload.secret))
        cred.strength_score = _strength_score(payload.secret_type, payload.secret)

    existing = await session.execute(select(ZACredentialHostLink).where(ZACredentialHostLink.credential_id == credential_id))
    for link in existing.scalars().all():
        await session.delete(link)
    for host_id in payload.host_ids:
        session.add(ZACredentialHostLink(credential_id=credential_id, host_id=host_id))

    await session.commit()
    await session.refresh(cred)

    await audit.log_action(
        user_id=actor_id, username=actor_name or "", action="credential.update",
        resource_type="credential", resource_id=cred.id,
        result="success",
    )
    return await _credential_out(session, cred)


@router.delete("/credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)])
async def delete_credential(
    credential_id: str,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    result = await session.execute(select(ZACredential).where(ZACredential.id == credential_id))
    cred = result.scalar_one_or_none()
    if cred is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="credential not found")

    await session.delete(cred)
    await session.commit()

    await audit.log_action(
        user_id=actor_id, username=actor_name or "", action="credential.delete",
        resource_type="credential", resource_id=credential_id,
        result="success",
    )


@router.get("/credentials/weak", response_model=list[CredentialOut], dependencies=[Depends(require_admin)])
async def list_weak_credentials(session: AsyncSession = Depends(get_session)):
    """Password credentials whose zxcvbn score is below the configured threshold (Feature 9)."""
    threshold = get_settings().weak_credential_threshold
    result = await session.execute(
        select(ZACredential).where(
            ZACredential.strength_score.isnot(None),
            ZACredential.strength_score < threshold,
        )
    )
    return [await _credential_out(session, c) for c in result.scalars().all()]


async def _credential_authorized_for_reveal(session: AsyncSession, credential_id: str, actor_id: str, settings) -> bool:
    """PCI DSS Phase A: reveal previously had no PAM gate at all beyond require_admin —
    any admin could reveal any credential's plaintext with zero za_authorization grant,
    via a path that never touches the terminal-service gateway. This checks, for every
    host the credential is linked to, whether the caller's resolved authorization for
    that host actually covers this credential + the "reveal" action."""
    import httpx
    from libs.servicetoken import mint

    links = await session.execute(
        select(ZACredentialHostLink.host_id).where(ZACredentialHostLink.credential_id == credential_id)
    )
    host_ids = [h for (h,) in links.all()]
    if not host_ids:
        return False
    token = mint("inventory-service", "identity-service", settings.service_jwt_secret)
    async with httpx.AsyncClient(base_url=settings.identity_service_url, timeout=5.0) as client:
        for host_id in host_ids:
            try:
                resp = await client.get(
                    "/api/v1/internal/authz/resolve",
                    params={"user_id": actor_id, "host_id": host_id},
                    headers={"X-Service-Token": token},
                )
            except httpx.HTTPError:
                continue
            if resp.status_code != 200:
                continue
            data = resp.json()
            cred_ids = data.get("credential_ids") or ([data["credential_id"]] if data.get("credential_id") else [])
            actions = data.get("actions") or []
            if credential_id in cred_ids and (not actions or "reveal" in actions):
                return True
    return False


@router.get("/credentials/{credential_id}/reveal", response_model=CredentialSecretOut, dependencies=[Depends(require_admin)])
async def reveal_credential(
    credential_id: str,
    session: AsyncSession = Depends(get_session),
    reveal_token: str = Header("", alias="X-Reveal-Token"),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
    actor_role: str = Header(default="user", alias="X-User-Role"),
    elevated_until: str = Header(default="", alias="X-Elevated-Until"),
):
    """MFA-gated secret reveal (Feature 6). Requires a short-lived reveal token minted by
    identity-service /auth/mfa/verify. The token is bound to BOTH the specific credential
    (``cid``) and the user (``sub``) it was minted for, so it cannot be replayed to reveal a
    different credential or by a different user.

    PCI DSS Phase A: the reveal token alone used to be sufficient — this also requires a
    real za_authorization grant covering "reveal" on this credential, same PAM gate SSH
    access already has, UNLESS the caller is admin/superadmin with an active JIT elevation
    (see terminal-service sessions.py's identical fallback)."""
    settings = get_settings()
    if not reveal_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="reveal token required")
    try:
        claims = jwt.decode(reveal_token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or expired reveal token") from exc
    if claims.get("purpose") != "credential_reveal":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="wrong token purpose")
    # Token must be scoped to THIS credential and THIS caller.
    if claims.get("cid") != credential_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="reveal token not valid for this credential")
    if actor_id and claims.get("sub") and claims["sub"] != actor_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="reveal token issued for a different user")

    result = await session.execute(select(ZACredential).where(ZACredential.id == credential_id))
    cred = result.scalar_one_or_none()
    if cred is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="credential not found")

    # Fail CLOSED, not open: a missing actor_id must never silently skip the PAM
    # check below (it did, briefly — caught by review before this shipped further).
    if not actor_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user identity required")

    elevation_used = False
    if not await _credential_authorized_for_reveal(session, credential_id, actor_id, settings):
        if actor_role in ("admin", "superadmin") and elevation_active(elevated_until):
            elevation_used = True
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="not authorized to reveal this credential — request access in Admin → Authorizations",
            )

    kind = _kind(cred.secret_type)
    try:
        secret = kind.decode(_decrypt_secret(cred))
    except VaultDecryptError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    await audit.log_action(
        user_id=actor_id, username=actor_name or "", action="credential.viewed",
        resource_type="credential", resource_id=cred.id,
        details={
            "event_type": "elevated_reveal" if elevation_used else "credential_viewed",
            "name": cred.name, "elevation_used": elevation_used,
        },
        result="success",
    )
    return CredentialSecretOut(id=cred.id, username=cred.username, secret_type=cred.secret_type, secret=secret)


@router.get("/internal/credentials/{credential_id}", response_model=CredentialOut)
async def internal_credential_meta(
    credential_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Credential metadata WITHOUT the secret — for callers that need to describe a
    login rather than use it.

    terminal-service's "Connect as" picker needs to label each account (default,
    sudo) and had only the secret endpoint to ask, which meant decrypting a secret
    purely to read a username. Every secret read is an audited, elevation-relevant
    event; doing it to render a badge both pollutes that record and hands the
    plaintext to a caller with no use for it.
    """
    result = await session.execute(select(ZACredential).where(ZACredential.id == credential_id))
    cred = result.scalar_one_or_none()
    if cred is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="credential not found")
    return await _credential_out(session, cred)


@router.get("/internal/credentials/{credential_id}/secret", response_model=CredentialSecretOut)
async def get_credential_secret(
    credential_id: str,
    session: AsyncSession = Depends(get_session),
    claims: dict = Depends(service_token_claims),
    x_user_id: str | None = Header(default=None),
    x_session_id: str | None = Header(default=None),
):
    """Decrypted secret — internal only, consumed by terminal-service/automation-service.

    This is the path every SSH session and automation job takes to obtain a
    plaintext credential, so it is the highest-volume egress of secret material
    in the platform. It is audited as `credential.secret_issued`: without it the
    only recorded credential access is the human `/reveal` UI path, and the
    question "who used credential X, and when" has no answer for machine access.

    Actor attribution comes from the token's signed `sub` claim. The X-User-Id
    header is only a migration fallback for callers not yet minting with a
    subject, and any value taken from it is marked unverified in the audit
    details — an unsigned assertion must never be recorded as though it were
    established fact.
    """
    requested_by = str(claims.get("iss", "unknown"))
    signed_user = claims.get("sub")
    actor_id = signed_user or x_user_id
    actor_verified = signed_user is not None
    result = await session.execute(select(ZACredential).where(ZACredential.id == credential_id))
    cred = result.scalar_one_or_none()
    if cred is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="credential not found")

    kind = _kind(cred.secret_type)
    try:
        secret = kind.decode(_decrypt_secret(cred))
    except VaultDecryptError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    # Recorded before the secret leaves the process, and critical=True so a
    # failed audit write aborts the request rather than releasing an unlogged
    # credential.
    try:
        await audit.log_action(
            user_id=actor_id,
            username="",
            action="credential.secret_issued",
            resource_type="credential",
            resource_id=cred.id,
            details={
                "event_type": "machine_access",
                "name": cred.name,
                "requested_by": requested_by,
                "secret_type": cred.secret_type,
                "actor_verified": actor_verified,
            },
            session_id=x_session_id,
            result="success",
            critical=True,
        )
    except audit.AuditWriteError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="cannot issue credential: audit log unavailable",
        ) from exc

    return CredentialSecretOut(
        id=cred.id, username=cred.username, secret_type=cred.secret_type, secret=secret,
        is_sudo=cred.is_sudo, is_push_account=cred.is_push_account,
    )


class _SecretUpdatePayload(pydantic.BaseModel):
    secret: dict


@router.put("/internal/credentials/{credential_id}/secret", status_code=status.HTTP_204_NO_CONTENT)
async def update_credential_secret(
    credential_id: str,
    payload: _SecretUpdatePayload,
    session: AsyncSession = Depends(get_session),
    actor_id: str = Header("", alias="X-User-Id"),
):
    """Re-encrypt and overwrite a credential secret — called exclusively by rotate_secret executor after all hosts updated."""
    result = await session.execute(select(ZACredential).where(ZACredential.id == credential_id))
    cred = result.scalar_one_or_none()
    if cred is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="credential not found")

    kind = _kind(cred.secret_type)
    kind.validate(payload.secret)

    # Archive the prior ciphertext (and its wrapped_dek, if envelope-encrypted —
    # cred.wrapped_dek is about to be overwritten below, and it's the only key
    # that can ever unwrap this archived ciphertext again) before overwriting.
    if cred.secret_ciphertext:
        session.add(ZACredentialHistory(
            credential_id=cred.id,
            secret_ciphertext=cred.secret_ciphertext,
            wrapped_dek=cred.wrapped_dek,
            rotated_by=actor_id or None,
        ))
        await session.flush()   # so the row just added is visible to the prune below

        # Keep only the most recent SECRET_HISTORY_KEEP prior secrets. The archive
        # was unbounded, which quietly turns a credential rotated weekly into a
        # growing pile of decryptable old passwords — every one of them still a
        # live secret for anything that never got re-keyed, and every one of them
        # something ops/rotate_vault_key.py has to keep rewrapping forever.
        # Deleting is done by explicit id after ordering, not by a bare OFFSET,
        # so rows with an identical rotated_at cannot make the window ambiguous.
        #
        # The ordering column is rotated_at. It was written as created_at, which
        # this model does not have, so the whole handler raised AttributeError and
        # rolled back: rotation returned 500 and archived nothing. It went unseen
        # because the only tests covering it were source-shape checks that looked
        # for the constant and the delete() call without ever executing either,
        # and end-to-end rotation was already failing earlier, on unreachable
        # demo hosts, so this never got a chance to run.
        keep_ids = (await session.execute(
            select(ZACredentialHistory.id)
            .where(ZACredentialHistory.credential_id == cred.id)
            .order_by(ZACredentialHistory.rotated_at.desc(), ZACredentialHistory.id.desc())
            .limit(SECRET_HISTORY_KEEP)
        )).scalars().all()
        if keep_ids:
            await session.execute(
                delete(ZACredentialHistory)
                .where(ZACredentialHistory.credential_id == cred.id)
                .where(ZACredentialHistory.id.notin_(keep_ids))
            )

    cred.secret_ciphertext, cred.wrapped_dek = _encrypt_secret(kind.encode(payload.secret))
    cred.strength_score = _strength_score(cred.secret_type, payload.secret)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    cred.updated_at = now
    cred.last_rotated_at = now
    await session.commit()

    await audit.log_action(
        user_id=actor_id or "system", username="", action="credential.secret_rotated",
        resource_type="credential", resource_id=credential_id,
        details={"event_type": "credential_rotated", "note": "secret rotated"},
        result="success",
    )
