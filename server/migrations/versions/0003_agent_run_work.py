"""What the runner must remember to call an agent again: the exact request, re-posts, deadlines and the delivered result."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "agent_run_work",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), sa.ForeignKey("case.id"), nullable=False),
        sa.Column("posted_at", TS),
        sa.Column("next_poll_at", TS, nullable=False),
        sa.Column("applied_at", TS),
        sa.Column("doc", pg.JSONB(), nullable=False),
    )
    op.create_index("ix_agent_run_work_tenant_id", "agent_run_work", ["tenant_id"])
    op.create_index("ix_agent_run_work_case_id", "agent_run_work", ["case_id"])


def downgrade() -> None:
    op.drop_table("agent_run_work")
