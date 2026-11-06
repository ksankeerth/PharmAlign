"""The mock regulator's own tables: proposals by idempotency key, licences, events and its failure setting"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "mock_regulator_proposal",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("seq", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("body", pg.JSONB(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("gov_ref", sa.Text()),
        sa.Column("resulting_version", sa.Text()),
        sa.Column("reason", sa.Text()),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_table(
        "mock_regulator_licence",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("doc", pg.JSONB(), nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_table(
        "mock_regulator_event",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("seq", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("at", TS, nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("object_id", sa.Text(), nullable=False),
    )
    op.create_table(
        "mock_regulator_setting",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", pg.JSONB(), nullable=False),
        sa.Column("set_at", TS, nullable=False),
    )


def downgrade() -> None:
    for name in ("mock_regulator_setting", "mock_regulator_event", "mock_regulator_licence", "mock_regulator_proposal"):
        op.drop_table(name)
