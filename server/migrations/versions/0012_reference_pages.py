"""Reference documents: the pages of the foreign approval a case relies on, so Sameness can read them.

A page's id is its anchor, such as `ref-doc-3:v1:p2`. The agent reads them through `agent_read.reference_page`,
scoped to the run's case by its reliance basis.
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

VIEW = """CREATE VIEW agent_read.reference_page AS
SELECT p.id AS anchor, p.basis_id, p.document_id, p.version, p.page, p.text
FROM reference_page p JOIN reliance_basis b ON b.id = p.basis_id AND b.tenant_id = p.tenant_id
WHERE p.tenant_id = agent_read.run_tenant() AND b.target_type = 'case' AND b.target_id = agent_read.run_case()"""


def upgrade() -> None:
    op.create_table(
        "reference_page",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("basis_id", sa.Text(), sa.ForeignKey("reliance_basis.id"), nullable=False),
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
    )
    op.create_index("ix_reference_page_tenant_id", "reference_page", ["tenant_id"])
    op.create_index("ix_reference_page_basis_id", "reference_page", ["basis_id"])
    op.execute(VIEW)
    op.execute("GRANT SELECT ON agent_read.reference_page TO pharmalign_agent")


def downgrade() -> None:
    op.execute("DROP VIEW agent_read.reference_page")
    op.drop_table("reference_page")
