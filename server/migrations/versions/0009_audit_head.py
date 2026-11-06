"""The end of each audit chain, so rows cut from the end are found"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_head",
        sa.Column("tenant_id", sa.Text(), primary_key=True),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("row_id", sa.Text(), nullable=False),
        sa.Column("row_hash", sa.Text(), nullable=False),
    )
    op.execute("""
        INSERT INTO audit_head (tenant_id, seq, row_id, row_hash)
        SELECT DISTINCT ON (tenant_id) tenant_id, seq, id, row_hash FROM audit_event ORDER BY tenant_id, seq DESC
    """)


def downgrade() -> None:
    op.drop_table("audit_head")
