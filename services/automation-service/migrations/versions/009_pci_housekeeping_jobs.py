"""PCI DSS Phase B: seed two new housekeeping jobs — authorization_ttl_sweep
(identity-service authorization expiry enforcement) and security_digest_report
(daily audit-log summary). Idempotent insert, matching 002_v1_1_housekeeping's
seed pattern.

Revision ID: auto_009
Revises: auto_008
Create Date: 2026-07-22
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "auto_009"
down_revision = "auto_008"
branch_labels = None
depends_on = None

_NEW_JOBS = [
    ("authorization_ttl_sweep", "Authorization TTL Sweep", "Disable authorizations past their date_expired", "0 */6 * * *"),
    ("security_digest_report", "Security Digest Report", "Broadcast a daily summary of audit log activity", "0 6 * * *"),
]


def upgrade() -> None:
    bind = op.get_bind()
    existing = {row[0] for row in bind.execute(sa.text("SELECT job_key FROM za_housekeeping_jobs")).fetchall()}
    hj = sa.table(
        "za_housekeeping_jobs",
        sa.column("id", sa.String),
        sa.column("job_key", sa.String),
        sa.column("display_name", sa.String),
        sa.column("description", sa.Text),
        sa.column("cron_expression", sa.String),
    )
    rows = [
        {"id": str(uuid.uuid4()), "job_key": key, "display_name": name, "description": desc, "cron_expression": cron}
        for key, name, desc, cron in _NEW_JOBS
        if key not in existing
    ]
    if rows:
        op.bulk_insert(hj, rows)


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM za_housekeeping_jobs WHERE job_key IN ('authorization_ttl_sweep', 'security_digest_report')")
    )
