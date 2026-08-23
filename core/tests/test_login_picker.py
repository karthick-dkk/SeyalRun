"""The "Connect as" picker: what it lists, what it remembers, what it never stores.

Three properties matter more than the layout:

  * the picker describes logins without READING them. It used to call the secret
    endpoint just to learn a username, decrypting a credential and writing an
    audited secret-access row every time the dialog opened. Rendering a dialog is
    not a secret access, and a log full of reads that were only label lookups
    makes the real ones harder to find.

  * "remember this login" stores a credential ID, never a secret. A PAM that
    cached passwords in localStorage would have given up the thing it exists to
    provide. The saved id is also re-checked against the live authorization, so a
    revoked grant cannot be resurrected from a stale browser entry.

  * manual accounts need an EXPLICIT grant. The first version treated an empty
    action list as unrestricted — matching the `ssh` check — which offered manual
    login on every host the caller had no authorization for at all, and the flow
    creates the credential before the session create rejects it. An unrestricted
    grant is not the same as no grant.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SESSIONS = ROOT / "services/terminal-service/app/api/sessions.py"
INV_CREDS = ROOT / "services/inventory-service/app/api/credentials.py"
PICKER = ROOT / "services/frontend/src/components/terminal/CredentialPicker.vue"
VIEW = ROOT / "services/frontend/src/views/TerminalView.vue"
AUTHZ_ADMIN = ROOT / "services/frontend/src/views/admin/AuthorizationsAdmin.vue"


def _authorized_credentials() -> str:
    src = SESSIONS.read_text()
    body = src[src.index("async def authorized_credentials"):]
    return body[: body.index("\n@router")]


def test_picker_endpoint_parses():
    """Guard against every assertion below passing vacuously."""
    assert "manual_allowed" in _authorized_credentials()


# ── describing a login is not reading it ─────────────────────────────────────

def test_picker_does_not_decrypt_secrets_to_render_labels():
    body = _authorized_credentials()
    assert "/secret" not in body, (
        "the picker must not read credential secrets — it needs a username and a "
        "couple of flags, and every secret read is an audited, elevation-relevant event"
    )
    assert re.search(r'_inventory_get\(f"/internal/credentials/\{cid\}"', body), \
        "expected the metadata endpoint"


def _code_only(text: str) -> str:
    """Drop comment lines. Several over-broad checks I wrote this session matched
    prose ABOUT a defect rather than the defect itself."""
    keep = []
    for line in text.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("#") or stripped.startswith("//"):
            continue
        keep.append(line)
    return "\n".join(keep)


def test_metadata_endpoint_returns_no_secret():
    src = INV_CREDS.read_text()
    start = src.index("async def internal_credential_meta")
    fn = _code_only(src[start: src.index("@router", start)])
    # the decorator immediately above the function declares the response model
    decorator = src[src.rindex("@router", 0, start): start]
    assert "CredentialOut" in decorator, \
        "the metadata route must return CredentialOut, which carries no secret"
    # Call-shaped, not substring: the docstring explains why this endpoint exists
    # by describing the decrypting it avoids, and a bare "decrypt" check flags the
    # explanation. That is the third time this session a check matched prose about
    # a defect instead of the defect.
    for banned in ("decrypt(", "decrypt_envelope(", "_decrypt_secret(", "CredentialSecretOut"):
        assert banned not in fn, f"the metadata endpoint must not touch secrets ({banned})"


def test_picker_reports_the_flags_it_renders():
    body = _authorized_credentials()
    for field in ("is_default", "is_sudo", "username"):
        assert field in body, f"the picker needs {field} to label a login"


# ── manual accounts need an explicit grant ───────────────────────────────────

def test_manual_account_requires_an_explicit_grant():
    body = _authorized_credentials()
    m = re.search(r'manual_allowed\s*=\s*(.+)', body)
    assert m, "manual_allowed not computed"
    expr = m.group(1).strip()
    assert expr == '"manual_account" in actions', (
        f"manual_allowed is {expr!r} — an empty action list means NO authorization, "
        "not an unrestricted one, and offering manual login there creates a "
        "credential on a host the caller cannot reach"
    )
    assert "not actions" not in expr


def test_manual_account_is_grantable_in_the_ui():
    """A permission the authorization screen cannot grant is a permission nobody has."""
    src = AUTHZ_ADMIN.read_text()
    m = re.search(r"const availableActions = \[([^\]]*)\]", src)
    assert m and "manual_account" in m.group(1)
    assert "manual_account:" in src, "the action needs a description explaining the risk"


# ── remembering a login must not remember a secret ───────────────────────────

def test_remembered_login_is_an_id_not_a_secret():
    src = PICKER.read_text()
    stored = re.search(r"map\[props\.host\.id\]\s*=\s*(\w+)", src)
    assert stored and stored.group(1) == "credentialId", (
        f"persisting {stored and stored.group(1)!r} — only the credential id may be stored"
    )
    # nothing secret-shaped may reach storage
    setitem = src[src.index("function writeStore"):][:300]
    for banned in ("password", "secret", "manual."):
        assert banned not in setitem, f"writeStore touches {banned}"


def test_manual_password_is_never_persisted_client_side():
    src = PICKER.read_text()
    assert "localStorage.setItem" in src, "sanity: storage is used for the id"
    # the only setItem call must be writeStore's
    assert src.count("localStorage.setItem") == 1
    assert "manual.password = ''" in src, "the typed secret must be cleared after use"


def test_storage_failure_degrades_instead_of_throwing():
    """Safari blocks storage in a third-party iframe, which is how this app runs
    embedded in Zabbix. A throw there would break every host click."""
    src = PICKER.read_text()
    assert src.count("catch") >= 2, "both read and write paths must tolerate blocked storage"


def test_auto_connect_revalidates_the_saved_login():
    """A revoked grant must not be resurrected from a stale browser entry."""
    src = VIEW.read_text()
    fn = src[src.index("const saved = rememberedCredential(host.id)"):][:400]
    assert "creds.some(c => c.id === saved)" in fn, (
        "the remembered id must be checked against the currently authorized list"
    )


# ── SSH and SFTP both start here ─────────────────────────────────────────────

def test_picker_offers_both_ssh_and_sftp():
    src = PICKER.read_text()
    assert "'ssh'" in src and "'sftp'" in src
    assert src.count("pick(cred,") == 2, "each login needs both an SSH and an SFTP action"


def test_sftp_opens_the_file_browser_on_the_same_session():
    """File transfer rides the SSH connection — there is no second thing to connect."""
    src = VIEW.read_text()
    fn = src[src.index("async function onCredPicked"):]
    fn = fn[: fn.index("\n}") + 2]
    assert "connectPane(paneId, host, p.credentialId)" in fn
    assert "showFiles.value = true" in fn


def test_manual_flow_does_not_claim_the_secret_is_discarded():
    """An earlier draft sent `ephemeral: !save`; no such field exists server-side,
    pydantic drops unknown keys, and the credential was kept anyway. A checkbox
    saying the secret is discarded while the vault keeps it is worse than not
    offering the choice."""
    src = PICKER.read_text()
    # Comment-stripped: the fix is explained in a comment that quotes the removed
    # flag, and a whole-file check reports the explanation as the defect.
    assert "ephemeral:" not in _code_only(src), "the ephemeral flag does nothing server-side"
    assert "saved to" in src, "the UI must state that the account is stored"
