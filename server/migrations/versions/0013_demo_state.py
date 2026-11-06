"""When the demo data was loaded, whether the real law text came with it, and whether a reset is running"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "demo_state",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("loaded_at", sa.DateTime(timezone=True)),
        sa.Column("with_library", sa.Boolean(), nullable=False),
        sa.Column("resetting", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.CheckConstraint("id = 1", name="ck_demo_state_one_row"),
    )


def downgrade() -> None:
    op.drop_table("demo_state")
