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

    async with sessionmaker() as session:
        rows = (
            await session.execute(
                text("SELECT id, secret_ciphertext, wrapped_dek FROM za_credentials")
            )
        ).all()

        envelope = [r for r in rows if r.wrapped_dek]
        legacy = [r for r in rows if not r.wrapped_dek]
        print(f"[*] {len(rows)} credential(s): {len(envelope)} envelope, {len(legacy)} legacy single-KEK")
        if dry_run:
            print("[*] DRY_RUN set — no changes written.")
            await engine.dispose()
            return

        # Envelope rows: only the wrapped DEK is re-wrapped. secret_ciphertext is
        # encrypted under the per-row DEK, not the KEK, so it must NOT be touched
        # — decrypting it with the KEK raises InvalidTag, which is exactly how the
        # previous version of this script died on the first envelope row.
        for row in envelope:
            # EnvKeyProvider stores the DEK as AES-GCM over its base64 text, so
            # this decrypts to that base64 string and re-encrypts the same string
            # under the new KEK — the DEK itself is unchanged, which is why the
            # credential ciphertext does not need rewriting.
            dek_b64 = decrypt_secret(row.wrapped_dek, old_key)
            await session.execute(
                text("UPDATE za_credentials SET wrapped_dek = :wd WHERE id = :cred_id"),
                {"wd": encrypt_secret(dek_b64, new_key), "cred_id": row.id},
            )

        # Legacy rows: the secret itself is under the KEK, so re-encrypt it.
        for row in legacy:
            plaintext = decrypt_secret(row.secret_ciphertext, old_key)
            await session.execute(
                text("UPDATE za_credentials SET secret_ciphertext = :ct WHERE id = :cred_id"),
                {"ct": encrypt_secret(plaintext, new_key), "cred_id": row.id},
            )

        await session.commit()

    await engine.dispose()
    print(f"[OK] Rotation complete: {len(envelope)} DEK(s) re-wrapped, {len(legacy)} secret(s) re-encrypted.")


if __name__ == "__main__":
    asyncio.run(main())
