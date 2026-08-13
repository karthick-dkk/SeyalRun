# Account Templates and Accounts — JumpServer-equivalent model

Decision (2026-08-13): match JumpServer PAM. Two entities, both holding secret
material, with a copy-on-create relationship between them.

## The model

| | JumpServer | SeyalRun today | Change |
|---|---|---|---|
| **Account Template** | name, username, secret_type, **secret**, privileged, auto-push | `ZACredentialTemplate` — metadata only, **no secret columns** | add encrypted secret |
| **Account** | bound to one asset, own username + secret | `ZACredential` (`credential_scope='host'`) + `za_credential_hosts` | already correct |

**The template is seed material, not a live shared credential.** Creating an
account from a template copies the secret into the new per-asset account, which
re-encrypts it under its own DEK. Each asset keeps its own row, so one asset's
compromise does not hand over every asset that used the same template. This is
why storing a secret on the template is safe here and would not have been under
a "one credential used live by every asset" reading.

`ZACredential.credential_scope` already exists (`host` | `template`) and stays —
it is a different axis and is not what a JumpServer Account Template is.

## Backend

1. **Migration** — `za_credential_templates` gains `secret_ciphertext TEXT NULL`
   and `wrapped_dek TEXT NULL`. Nullable because existing templates have no
   secret and must keep working as pure defaults profiles.

2. **Same vault path, no second scheme.** Encrypt with `app/vault.encrypt_envelope`,
   exactly as `ZACredential` does — per-row DEK, KEK-wrapped. Anything else
   creates a second encryption story for `ops/rotate_vault_key.py` to miss,
   which is precisely how R-6 happened.

3. **`ops/rotate_vault_key.py` must rewrap the new table.** It currently rotates
   `za_credentials` and `za_credential_history`. A template secret it does not
   rewrap becomes undecryptable on the next KEK rotation. Add
   `za_credential_templates` to `TABLES` in the same change as the migration,
   not afterwards.

4. **Copy on create.** `POST /credentials` with `template_id` and no secret:
   decrypt the template secret, re-encrypt under a fresh DEK for the new
   credential. The template's ciphertext is never shared or referenced by the
   account.

5. **Audit.** Template create/update/delete get chained rows carrying `result`
   (R-10). Reading a template secret for a copy is a secret access and must be
   audited as one — `credential.template_secret_used`, `critical=True`, so a
   copy that cannot be logged does not proceed. This mirrors
   `credential.secret_issued`.

6. **Reveal is a separate right.** Revealing a template secret in the UI goes
   through the same elevation gate as `POST /credentials/{id}/reveal`, not the
   plain admin check.

## Frontend

1. **Account Template form** gains a secret field (password / SSH key), matching
   the credential form's control, with the strength meter the credential form
   already uses.

2. **Asset create** offers, per the request: **create a new credential** for this
   asset, or **attach from a template** — the second populates username and
   secret from the chosen template and creates a normal per-asset account.

3. **Naming.** "Account Template" and "Account" throughout, matching JumpServer,
   so the two concepts stop colliding with `credential_scope='template'`.

## Verification

- A template created with a password, then an account created from it: the
  account decrypts to the same plaintext and has its **own** `wrapped_dek`
  (not the template's).
- `rotate_vault_key.py --dry-run` reports the template rows; after a real
  rotation both template and account secrets still decrypt.
- Deleting a template does not affect accounts already created from it.
- The audit chain still verifies with the new rows present.
