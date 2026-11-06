"""Stemmed words beside each chunk, for keyword search. Chunks can't change, so this is a table of its own. Agents read it through a view."""
from alembic import op
import sqlalchemy as sa

from app.knowledge.keywords import tokens

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chunk_terms",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("chunk_id", sa.Text(), sa.ForeignKey("chunk.id"), nullable=False),
        sa.Column("tokens", sa.Text(), nullable=False),
        sa.UniqueConstraint("chunk_id", name="uq_chunk_terms_chunk_id"),
    )
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, text FROM chunk")).all()
    for i in range(0, len(rows), 500):
        bind.execute(
            sa.text("INSERT INTO chunk_terms (id, chunk_id, tokens) VALUES (:id, :id, :tokens)"),
            [{"id": r.id, "tokens": " ".join(tokens(r.text))} for r in rows[i:i + 500]],
        )
    op.execute("CREATE VIEW agent_read.kb_chunk_terms AS SELECT k.anchor, t.tokens FROM agent_read.kb_chunk k JOIN chunk_terms t ON t.chunk_id = k.anchor")
    op.execute("GRANT SELECT ON agent_read.kb_chunk_terms TO pharmalign_agent")


def downgrade() -> None:
    op.execute("DROP VIEW agent_read.kb_chunk_terms")
    op.drop_table("chunk_terms")
