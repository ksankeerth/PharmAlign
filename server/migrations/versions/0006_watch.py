"""Foreign watch: a notice's identity and its text, when a source last yielded, and the items a staged source holds"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("watch_source", sa.Column("last_item_at", TS))
    op.add_column("foreign_notice", sa.Column("notice_key", sa.Text()))
    op.add_column("foreign_notice", sa.Column("source_id", sa.Text()))
    op.create_unique_constraint("uq_foreign_notice_tenant_id_notice_key", "foreign_notice", ["tenant_id", "notice_key"])
    op.add_column("foreign_notice_revision", sa.Column("text", sa.Text()))
    op.create_table(
        "watch_staged_item",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), sa.ForeignKey("watch_source.id"), nullable=False),
        sa.Column("notice", pg.JSONB(), nullable=False),
        sa.Column("staged_at", TS, nullable=False),
        sa.Column("taken_at", TS),
    )
    op.create_index("ix_watch_staged_item_tenant_id", "watch_staged_item", ["tenant_id"])
    op.create_index("ix_watch_staged_item_source_id", "watch_staged_item", ["source_id"])


def downgrade() -> None:
    op.drop_table("watch_staged_item")
    op.drop_column("foreign_notice_revision", "text")
    op.drop_constraint("uq_foreign_notice_tenant_id_notice_key", "foreign_notice")
    op.drop_column("foreign_notice", "source_id")
    op.drop_column("foreign_notice", "notice_key")
    op.drop_column("watch_source", "last_item_at")
