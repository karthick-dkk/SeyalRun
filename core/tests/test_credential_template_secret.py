"""Account Templates hold a secret, and every path that touches it is guarded.

The reported bug was plain: saving a password or SSH key on an Account Template
kept nothing, because the table had no secret columns at all — the write went to
a stray attribute on the ORM object and was discarded. JumpServer PAM's model,
which this now matches, is that a template DOES carry a secret and creating an
Account from it COPIES that secret into the new per-asset account under that
account's own DEK. The template is seed material, not a live shared credential.

Adding a stored secret adds four ways to leak it, and these pin all four:

1. It must not come back from the list endpoint. CredentialTemplateOut used to
   inherit CredentialTemplateCreate, so the obvious way to add the field would
   have returned every template secret to any admin with no reveal token, no
   elevation and no audit row.
2. It must be encrypted the same way credentials are — one envelope scheme, so
   ops/rotate_vault_key.py has one thing to rewrap rather than two. A wrapped
   secret that script does not know about is undecryptable after the next KEK
   rotation, which is exactly what R-6 was.
3. Reading one to copy it is a secret access and must be audited as one.
4. Revealing one needs the elevation gate, not the plain admin check.

Source-shape checks: the invariant suite has no database, and every failure here
is structural — a missing column, an inherited field, an unaudited read.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INV = ROOT / "services/inventory-service"
CREDS_API = INV / "app/api/credentials.py"
SCHEMAS = INV / "app/schemas.py"
MODELS = INV / "app/models.py"
ROTATE = ROOT.parent / "ops/rotate_vault_key.py"
MIGRATIONS = INV / "migrations/versions"


def _template_model() -> str:
    src = MODELS.read_text()
    start = src.index("class ZACredentialTemplate")
    return src[start : src.index("\nclass ", start + 1)]


def test_template_model_parses():
    """Guard against the slice above silently returning nothing."""
    assert "__tablename__" in _template_model()


# ── 1. the secret must not be readable through the ordinary endpoints ────────

def test_template_out_does_not_inherit_the_write_only_secret():
    src = SCHEMAS.read_text()
    out = src[src.index("class CredentialTemplateOut") :]
    out = out[: out.index("\n\nclass ")]
    base = re.match(r"class CredentialTemplateOut\((\w+)\)", out).group(1)
    assert base != "CredentialTemplateCreate", (
        "CredentialTemplateOut must not inherit the Create schema — the write-only "
        "`secret` field would be serialised by GET /credential-templates"
    )
    # anchored: `has_secret: bool` is fine and expected, a bare `secret:` field is not
    assert not re.search(r"^\s+secret:", out, re.M), "the response schema must not carry a secret field"
    # It should still say whether one exists, without disclosing it.
    assert "has_secret" in out


def test_create_schema_carries_the_secret_and_base_does_not():
    src = SCHEMAS.read_text()
    create = src[src.index("class CredentialTemplateCreate") : src.index("class CredentialTemplateOut")]
    assert re.search(r"^\s+secret:\s*dict", create, re.M), "the Create schema must accept a secret"
    base = src[src.index("class CredentialTemplateBase") : src.index("class CredentialTemplateCreate")]
    assert not re.search(r"^\s+secret:", base, re.M), "a secret on the shared Base would leak straight into Out"


def test_has_secret_reports_existence_not_content():
    model = _template_model()
    assert "def has_secret" in model
    body = model[model.index("def has_secret") :]
    assert "bool(" in body, "has_secret must be a boolean, not the ciphertext"
    assert "return self.secret_ciphertext" not in body.replace("bool(self.secret_ciphertext)", "")


# ── 2. one encryption scheme, and the rotation script knows about it ─────────

def test_template_secret_columns_exist_with_their_dek():
    model = _template_model()
    assert "secret_ciphertext" in model
    assert "wrapped_dek" in model, (
        "an envelope ciphertext without its wrapped DEK cannot be decrypted"
    )
    for banned in ("plaintext", "secret_plain", "password:"):
        assert banned not in model, f"template row must not carry {banned!r}"


def test_template_uses_the_same_envelope_helpers_as_credentials():
    src = CREDS_API.read_text()
    fn = src[src.index("def _encrypt_template_secret") : src.index("def _decrypt_template_secret")]
    assert "_encrypt_secret(" in fn, "templates must use the shared envelope helper, not a second scheme"


def test_kek_rotation_covers_the_template_table():
    """R-6's exact shape: a table of wrapped secrets the rotation skips becomes
    permanently undecryptable at the next rotation."""
    src = ROTATE.read_text()
    m = re.search(r"TABLES\s*=\s*\(([^)]*)\)", src)
    assert m, "rotate_vault_key.py must declare the tables it rotates"
    assert "za_credential_templates" in m.group(1)


def test_rotation_tolerates_a_template_with_no_secret():
    """Template secrets are nullable — a defaults-only template has NULL in both
    columns. Bucketing those as 'legacy' hands decrypt_secret(None) to the
    pre-flight and aborts the whole rotation over rows holding nothing."""
    src = ROTATE.read_text()
    assert '"empty"' in src, "rotation must bucket rows that carry no secret at all"
    legacy = re.search(r'"legacy":\s*\[([^\]]*)\]', src).group(1)
    assert "secret_ciphertext" in legacy, (
        "the legacy bucket must require a secret to be present, not merely a missing wrapped_dek"
    )
    # The post-verify loop reads every row back and must skip the empty ones too.
    verify = src[src.index("Post-verification") - 900 :]
    assert "elif row.secret_ciphertext" in verify, "post-verification must skip secret-less rows"


def test_migration_adds_both_columns_nullable():
    files = sorted(MIGRATIONS.glob("*_credential_template_secret.py"))
    assert files, "expected a migration adding the template secret columns"
    src = files[-1].read_text()
    for col in ("secret_ciphertext", "wrapped_dek"):
        assert col in src
    assert "nullable=True" in src, "existing defaults-only templates must keep working"


# ── 3 & 4. copying is audited; revealing is elevation-gated ─────────────────

def test_copying_a_template_secret_is_audited_critically():
    src = CREDS_API.read_text()
    assert "credential.template_secret_used" in src, (
        "reading a template secret to seed an account is a secret access and must be audited"
    )
    block = src[src.index("credential.template_secret_used") :][:600]
    assert "critical=True" in block, (
        "a copy that cannot be logged must not proceed — same rule as credential.secret_issued"
    )


def test_copy_on_create_reencrypts_rather_than_sharing_ciphertext():
    """The account must get its own DEK. Assigning the template's ciphertext and
    wrapped_dek across would make one compromise unlock every seeded asset."""
    src = CREDS_API.read_text()
    create = src[src.index("async def create_credential(") :]
    create = create[: create.index("\n@router")]
    assert "_encrypt_secret(kind.encode(secret))" in create, (
        "the copied secret must be re-encrypted under a fresh DEK for this account"
    )
    assert "tmpl.wrapped_dek" not in create, "the template's DEK must never be reused"


def test_template_reveal_is_elevation_gated():
    src = CREDS_API.read_text()
    fn = src[src.index("async def reveal_credential_template(") :]
    fn = fn[: fn.index("\n@router")]
    assert "reveal token required" in fn, "must require an MFA-minted reveal token"
    assert "elevation_active(elevated_until)" in fn, "must require an active elevation"
    assert 'claims.get("cid") != template_id' in fn, "token must be bound to this template"
    assert "critical=True" in fn, "handing out a template secret must be logged or refused"


def test_template_crud_is_audited():
    """Templates were entirely unaudited before they held secrets."""
    src = CREDS_API.read_text()
    for action in ("credential_template.create", "credential_template.update",
                   "credential_template.delete"):
        assert action in src, f"{action} must produce an audit row"


def test_template_update_does_not_wipe_the_stored_secret():
    """PUT with an empty secret means "keep it" — and a blanket setattr over
    model_dump() would both skip the ciphertext and write a stray `secret`
    attribute onto the ORM object, which is the original reported bug."""
    src = CREDS_API.read_text()
    fn = src[src.index("async def update_credential_template(") :]
    fn = fn[: fn.index("\n@router")]
    assert 'exclude={"secret"}' in fn
    assert "if payload.secret:" in fn, "only a non-empty secret may replace the stored one"
