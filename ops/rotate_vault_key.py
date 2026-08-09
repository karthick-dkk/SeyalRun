#!/usr/bin/env python3
"""Re-encrypts every za_credentials.secret_ciphertext under a new
ZA_VAULT_PASSWORD/ZA_VAULT_SALT.

Invoked by ops/rotate-vault-key.sh inside the inventory-service container,
which already has libs/dbcore and its dependencies (cryptography,
SQLAlchemy, asyncpg/aiomysql) available.
"""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import text

from libs.dbcore import build_database_url, decrypt_secret, derive_key, encrypt_secret, make_engine, make_sessionmaker


async def main() -> None:
    db_engine = os.environ["DB_ENGINE"]
    url = build_database_url(
        db_engine,
        os.environ["DB_USER"],
        os.environ["DB_PASSWORD"],
        os.environ["DB_HOST"],
        os.environ["DB_PORT"],
        os.environ["INVENTORY_DB_NAME"],
    )
    engine = make_engine(url, db_engine, os.environ.get("DB_SSLMODE", "require"))
    sessionmaker = make_sessionmaker(engine)

    old_key = derive_key(os.environ["OLD_ZA_VAULT_PASSWORD"], os.environ["OLD_ZA_VAULT_SALT"])
    new_key = derive_key(os.environ["ZA_VAULT_PASSWORD"], os.environ["ZA_VAULT_SALT"])

    dry_run = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")

    # Every table that stores key material wrapped under the KEK must be rotated.
    # Missing one is silent and permanent: za_credential_history keeps its own
    # wrapped_dek per archived version (inventory-service/app/models.py:176), and
    # once the old KEK is discarded no DEK anywhere can unwrap those rows again.
    # An earlier version of this script rotated only za_credentials and printed
    # success, quietly orphaning the whole archive.
    #
    #   TABLE                  | ENVELOPE ROW (wrapped_dek set) | LEGACY ROW
    #   -----------------------|--------------------------------|-------------------------
    #   za_credentials         | rewrap wrapped_dek             | re-encrypt secret_ciphertext
    #   za_credential_history  | rewrap wrapped_dek             | re-encrypt secret_ciphertext
    #
    # Envelope rows must NOT have secret_ciphertext touched: it is encrypted under
    # the per-row DEK, not the KEK, so decrypting it with the KEK raises InvalidTag.
    TABLES = ("za_credentials", "za_credential_history")

    async with sessionmaker() as session:
        plan: dict[str, dict] = {}
        for table in TABLES:
            rows = (
                await session.execute(
                    text(f"SELECT id, secret_ciphertext, wrapped_dek FROM {table}")
                )
            ).all()
            plan[table] = {
                "envelope": [r for r in rows if r.wrapped_dek],
                "legacy": [r for r in rows if not r.wrapped_dek],
            }
            print(
                f"[*] {table}: {len(rows)} row(s) — "
                f"{len(plan[table]['envelope'])} envelope, {len(plan[table]['legacy'])} legacy single-KEK"
            )

        if dry_run:
            print("[*] DRY_RUN set — no changes written.")
            await engine.dispose()
            return

        # Pre-flight: prove every row is readable under the OLD key before writing
        # anything. Without this the transaction can fail partway through a large
        # rotation and, while the rollback keeps the data safe, the operator learns
        # about an unreadable row only after a long and alarming failure.
        for table in TABLES:
            for row in plan[table]["envelope"]:
                decrypt_secret(row.wrapped_dek, old_key)
            for row in plan[table]["legacy"]:
                decrypt_secret(row.secret_ciphertext, old_key)
        total = sum(len(v["envelope"]) + len(v["legacy"]) for v in plan.values())
        print(f"[*] Pre-flight OK — all {total} row(s) decrypt under the old key.")

        rewrapped = reencrypted = 0
        for table in TABLES:
            # EnvKeyProvider stores the DEK as AES-GCM over its base64 text, so this
            # decrypts to that base64 string and re-encrypts the same string under
            # the new KEK. The DEK itself is unchanged, which is the whole point of
            # the hierarchy: only small blobs move, never the credential ciphertext.
            for row in plan[table]["envelope"]:
                dek_b64 = decrypt_secret(row.wrapped_dek, old_key)
                await session.execute(
                    text(f"UPDATE {table} SET wrapped_dek = :wd WHERE id = :row_id"),
                    {"wd": encrypt_secret(dek_b64, new_key), "row_id": row.id},
                )
                rewrapped += 1

            for row in plan[table]["legacy"]:
                plaintext = decrypt_secret(row.secret_ciphertext, old_key)
                await session.execute(
                    text(f"UPDATE {table} SET secret_ciphertext = :ct WHERE id = :row_id"),
                    {"ct": encrypt_secret(plaintext, new_key), "row_id": row.id},
                )
                reencrypted += 1

        await session.commit()

    # Post-verification on a fresh session: read every row back and confirm it
    # decrypts under the NEW key. "Rotation complete" is only trustworthy if
    # something actually checked, rather than assuming the writes were correct.
    async with sessionmaker() as session:
        verified = 0
        for table in TABLES:
            rows = (
                await session.execute(
                    text(f"SELECT id, secret_ciphertext, wrapped_dek FROM {table}")
                )
            ).all()
            for row in rows:
                if row.wrapped_dek:
                    decrypt_secret(row.wrapped_dek, new_key)
                else:
                    decrypt_secret(row.secret_ciphertext, new_key)
                verified += 1
        print(f"[*] Post-verification OK — all {verified} row(s) decrypt under the new key.")

    await engine.dispose()
    print(
        f"[OK] Rotation complete across {len(TABLES)} table(s): "
        f"{rewrapped} DEK(s) re-wrapped, {reencrypted} secret(s) re-encrypted."
    )


if __name__ == "__main__":
    asyncio.run(main())
