"""Gateways become hosts.

A gateway was a za_gateways row with its own shape: an address, a username and a
credential id, and nothing else. That meant it could not appear in Assets, could
not belong to an asset group, could not be authorized, and could not carry a
credential through the normal credential paths — every one of those had to be
special-cased or simply did not exist for gateways.

It is an ordinary host that happens to be used as a jump point, so it is modelled
as one: host_type = "gateway". Everything hosts already have (groups, zones,
credentials, authorization, reachability) then applies to it for free.

Existing za_gateways rows are COPIED into za_hosts rather than moved, and the old
table is left in place. zone_gateway_chain prefers gateway hosts and falls back to
za_gateways when a zone has none, so a deployment that has not been reviewed keeps
connecting exactly as before. Dropping the table is a later, separate decision
once the copies are confirmed.

Revision ID: 013
Revises: 012
Create Date: 2026-08-23
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("za_hosts")}
    if "host_type" not in cols:
        op.add_column("za_hosts", sa.Column("host_type", sa.String(20),
                                            nullable=False, server_default="server"))
    if "gateway_order" not in cols:
        op.add_column("za_hosts", sa.Column("gateway_order", sa.Integer(),
                                            nullable=False, server_default="0"))

    tables = set(sa.inspect(bind).get_table_names())
    if "za_gateways" not in tables:
        return

    # Copy, keyed on name+address so re-running cannot duplicate.
    existing = {
        (r[0], r[1]) for r in bind.execute(
            sa.text("SELECT name, ip FROM za_hosts WHERE host_type = 'gateway'")
        )
    }
    rows = bind.execute(sa.text(
        "SELECT id, zone_id, name, host, port, username, credential_id FROM za_gateways"
    )).mappings().all()
    for i, gw in enumerate(rows):
        if (gw["name"], gw["host"]) in existing:
            continue
        host_id = str(uuid.uuid4())
        bind.execute(
            sa.text(
                "INSERT INTO za_hosts (id, name, ip, port, os_type, host_type, gateway_order,"
                " enabled, zone_id, is_production) VALUES"
                " (:id, :name, :ip, :port, 'linux', 'gateway', :ord, true, :zone_id, false)"
            ),
            {"id": host_id, "name": gw["name"], "ip": gw["host"],
             "port": gw["port"] or 22, "ord": i, "zone_id": gw["zone_id"]},
        )
        # Carry the login across so the copied gateway is usable immediately.
        if gw["credential_id"]:
            bind.execute(
                sa.text(
                    "INSERT INTO za_credential_host_links (credential_id, host_id)"
                    " VALUES (:cid, :hid)"
                ),
                {"cid": gw["credential_id"], "hid": host_id},
            )


def downgrade() -> None:
    op.execute("DELETE FROM za_hosts WHERE host_type = 'gateway'")
    op.drop_column("za_hosts", "gateway_order")
    op.drop_column("za_hosts", "host_type")
