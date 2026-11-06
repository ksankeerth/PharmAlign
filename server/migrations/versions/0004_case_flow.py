"""Why a grant went ahead without a fresh foreign read, and the agent's own session id for an assistant chat."""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("decision", sa.Column("foreign_status_reason", sa.Text()))
    op.add_column("assistant_session", sa.Column("agent_session_id", sa.Text()))


def downgrade() -> None:
    op.drop_column("assistant_session", "agent_session_id")
    op.drop_column("decision", "foreign_status_reason")
