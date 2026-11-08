"""A vector index for gemini-embedding-001, whose vectors have 1536 numbers (pgvector indexes at most 2000)."""
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

MODEL, DIMS = "gemini-embedding-001", 1536
INDEX = "ix_chunk_embedding_gemini_embedding_001"


def upgrade() -> None:
    op.execute(f"CREATE INDEX {INDEX} ON chunk_embedding USING hnsw ((embedding::vector({DIMS})) vector_cosine_ops) WHERE model = '{MODEL}'")


def downgrade() -> None:
    op.execute(f"DROP INDEX {INDEX}")
