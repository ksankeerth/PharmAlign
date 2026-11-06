"""A chat may have no case: it then reads only the knowledge base."""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("assistant_session", "case_id", nullable=True)
    op.add_column("assistant_session", sa.Column("kb_snapshot_id", sa.Text()))


def downgrade() -> None:
    op.execute("DELETE FROM assistant_exchange WHERE session_id IN (SELECT id FROM assistant_session WHERE case_id IS NULL)")
    op.execute("DELETE FROM assistant_session WHERE case_id IS NULL")
    op.alter_column("assistant_session", "case_id", nullable=False)
    op.drop_column("assistant_session", "kb_snapshot_id")
