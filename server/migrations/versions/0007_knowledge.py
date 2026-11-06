"""Knowledge: stored files, the latest quality result, a job's parsed output, the vector index and the agent's vector view.

The columns here repeat the entries in app/tables.py, and a test compares the two.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
NOW = sa.text("now()")

# Vectors are stored without a fixed size, so each model gets its own partial index on a cast to its size.
INDEXED_MODELS = {"hash-256": 256, "bge-m3": 1024}


def upgrade() -> None:
    op.create_table(
        "document_file",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("document_version_id", sa.Text(), sa.ForeignKey("document_version.id"), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("document_version_id", name="uq_document_file_document_version_id"),
    )
    op.create_table(
        "quality_report",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("document_version_id", sa.Text(), sa.ForeignKey("document_version.id"), nullable=False),
        sa.Column("ingest_job_id", sa.Text(), sa.ForeignKey("ingest_job.id"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("held", sa.Boolean(), nullable=False),
        sa.Column("doc", pg.JSONB(), nullable=False),
        sa.Column("sketch", pg.JSONB(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("document_version_id", name="uq_quality_report_document_version_id"),
    )
    op.create_table(
        "ingest_output",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("ingest_job_id", sa.Text(), sa.ForeignKey("ingest_job.id"), nullable=False),
        sa.Column("document_version_id", sa.Text(), sa.ForeignKey("document_version.id"), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("raw", pg.JSONB(), nullable=False),
        sa.Column("chunks", pg.JSONB()),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("ingest_job_id", name="uq_ingest_output_ingest_job_id"),
    )
    op.execute("CREATE TRIGGER document_file_fixed BEFORE UPDATE OR DELETE ON document_file FOR EACH ROW EXECUTE FUNCTION refuse_change()")
    op.execute("ALTER TABLE chunk_embedding ADD CONSTRAINT ck_chunk_embedding_dims CHECK (vector_dims(embedding) = dims)")
    for model, dims in INDEXED_MODELS.items():
        name = "ix_chunk_embedding_" + model.replace("-", "_")
        op.execute(
            f"CREATE INDEX {name} ON chunk_embedding USING hnsw ((embedding::vector({dims})) vector_cosine_ops) WHERE model = '{model}'"
        )
    # What an agent may rank: the vectors of the chunks its run can already read, and no others.
    op.execute(
        "CREATE VIEW agent_read.kb_chunk_vector AS "
        "SELECT k.anchor, e.model, e.dims, e.embedding FROM agent_read.kb_chunk k JOIN chunk_embedding e ON e.chunk_id = k.anchor"
    )
    op.execute("GRANT SELECT ON agent_read.kb_chunk_vector TO pharmalign_agent")


def downgrade() -> None:
    op.execute("DROP VIEW agent_read.kb_chunk_vector")
    for model in INDEXED_MODELS:
        op.execute("DROP INDEX ix_chunk_embedding_" + model.replace("-", "_"))
    op.execute("ALTER TABLE chunk_embedding DROP CONSTRAINT ck_chunk_embedding_dims")
    op.drop_table("ingest_output")
    op.drop_table("quality_report")
    op.drop_table("document_file")
