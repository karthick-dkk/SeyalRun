"""Make za_audit_logs append-only at the database layer — risk register R-5

The hash chain makes tampering *detectable*, but only when someone runs
/audit/verify, and nothing stopped the application's own DB role from issuing
UPDATE or DELETE. PCI DSS Req 10.5 asks for logs to be protected from
modification, not merely for modification to be discoverable afterwards.

Postgres: a trigger raising on UPDATE/DELETE. Chosen over REVOKE because the
application frequently owns the table it connects as, and an owner's privileges
cannot be revoked from itself — a REVOKE would appear to succeed while changing
nothing. A trigger binds regardless of role, including the owner and (unlike
column-level grants) regardless of how the statement is issued. Superusers can
still disable it, which is the documented residual risk.

MySQL: equivalent BEFORE UPDATE / BEFORE DELETE triggers using SIGNAL.

Nothing in the codebase deletes or updates this table today — the retention
endpoint (/internal/audit/retention-status) deliberately only *counts* rows past
the threshold, because removing one breaks verify_chain() for every row after
it. So this trigger does not restrict any existing behaviour; it enforces at the
database an invariant the application already honours by convention.

When real archival lands it must copy-not-move (chain intact). If a future
operation genuinely needs to remove rows, it has to drop the trigger, verify the
chain, archive, and re-create — which is the point: that sequence is deliberate
and auditable rather than an incidental DELETE.

Revision ID: 022
Revises: 021
Create Date: 2026-08-08
"""

from __future__ import annotations

from alembic import op

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None

# A single % is plpgsql's placeholder for the TG_OP argument. Doubling it makes
# plpgsql read a literal percent, leaving zero placeholders for one argument and
# failing with "too many parameters specified for RAISE" — asyncpg binds with $1,
# so there is no driver-level %-interpolation to escape against here.
PG_FN = """
CREATE OR REPLACE FUNCTION za_audit_logs_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'za_audit_logs is append-only (% attempted)', TG_OP
        USING HINT = 'Audit rows are hash-chained; editing or deleting one breaks verify_chain().';
END;
$$ LANGUAGE plpgsql;
"""

PG_TRIGGER = """
CREATE TRIGGER za_audit_logs_no_modify
BEFORE UPDATE OR DELETE ON za_audit_logs
FOR EACH ROW EXECUTE FUNCTION za_audit_logs_append_only();
"""

MYSQL_UPDATE = """
CREATE TRIGGER za_audit_logs_no_update BEFORE UPDATE ON za_audit_logs
FOR EACH ROW SIGNAL SQLSTATE '45000'
SET MESSAGE_TEXT = 'za_audit_logs is append-only (UPDATE attempted)';
"""

MYSQL_DELETE = """
CREATE TRIGGER za_audit_logs_no_delete BEFORE DELETE ON za_audit_logs
FOR EACH ROW SIGNAL SQLSTATE '45000'
SET MESSAGE_TEXT = 'za_audit_logs is append-only (DELETE attempted)';
"""


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(PG_FN)
        op.execute("DROP TRIGGER IF EXISTS za_audit_logs_no_modify ON za_audit_logs")
        op.execute(PG_TRIGGER)
    elif dialect == "mysql":
        op.execute("DROP TRIGGER IF EXISTS za_audit_logs_no_update")
        op.execute("DROP TRIGGER IF EXISTS za_audit_logs_no_delete")
        op.execute(MYSQL_UPDATE)
        op.execute(MYSQL_DELETE)
    # Any other dialect: no-op rather than a false sense of protection. The
    # control mapping records this as engine-dependent.


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS za_audit_logs_no_modify ON za_audit_logs")
        op.execute("DROP FUNCTION IF EXISTS za_audit_logs_append_only()")
    elif dialect == "mysql":
        op.execute("DROP TRIGGER IF EXISTS za_audit_logs_no_update")
        op.execute("DROP TRIGGER IF EXISTS za_audit_logs_no_delete")
