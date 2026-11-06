"""People, grants, audit log, domain events, jobs and idempotency keys."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "person",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("seq", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("view", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_person_tenant_id", "person", ["tenant_id"])

    op.create_table(
        "role_grant",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("person_id", sa.Text(), sa.ForeignKey("person.id"), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("discipline", sa.Text(), nullable=True),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
    )
    op.create_index("ix_role_grant_person_id", "role_grant", ["person_id"])

    op.create_table(
        "audit_event",
        sa.Column("seq", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("id", sa.Text(), nullable=False, unique=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("at", TS, nullable=False),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=True),
        sa.Column("role_used", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("object_type", sa.Text(), nullable=False),
        sa.Column("object_id", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=True),
        sa.Column("before", pg.JSON(none_as_null=True), nullable=True),
        sa.Column("after", pg.JSON(none_as_null=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("prev_hash", sa.Text(), nullable=True),
        sa.Column("row_hash", sa.Text(), nullable=False),
        sa.UniqueConstraint("tenant_id", "prev_hash", name="uq_audit_event_chain", postgresql_nulls_not_distinct=True),
    )
    op.create_index("ix_audit_event_tenant_case", "audit_event", ["tenant_id", "case_id"])
    op.execute("""
        CREATE FUNCTION audit_event_refuse() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_event is append-only' USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER audit_event_no_change BEFORE UPDATE OR DELETE ON audit_event
        FOR EACH ROW EXECUTE FUNCTION audit_event_refuse()
    """)
    op.execute("""
        CREATE TRIGGER audit_event_no_truncate BEFORE TRUNCATE ON audit_event
        FOR EACH STATEMENT EXECUTE FUNCTION audit_event_refuse()
    """)

    op.create_table(
        "domain_event",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("seq", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=True),
        sa.Column("recipient", sa.Text(), nullable=True),
        sa.Column("at", TS, nullable=False),
        sa.Column("doc", pg.JSONB(), nullable=False),
    )
    op.create_index("ix_domain_event_stream", "domain_event", ["tenant_id", "type", "case_id"])

    op.create_table(
        "job",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("run_at", TS, nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.Text(), nullable=True),
        sa.Column("lease_token", sa.Text(), nullable=True),
        sa.Column("lease_expires_at", TS, nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("finished_at", TS, nullable=True),
    )
    op.create_index("ix_job_due", "job", ["status", "run_at"])

    op.create_table(
        "idempotency_key",
        sa.Column("tenant_id", sa.Text(), primary_key=True),
        sa.Column("actor_id", sa.Text(), primary_key=True),
        sa.Column("operation", sa.Text(), primary_key=True),
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("idempotency_key")
    op.drop_table("job")
    op.drop_table("domain_event")
    op.drop_table("audit_event")
    op.execute("DROP FUNCTION audit_event_refuse()")
    op.drop_table("role_grant")
    op.drop_table("person")
