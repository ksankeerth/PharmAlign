"""Columns for fields the contract now names: a source's severity floor, a notice's own reference, the case a match opened, and the pack draft a gap opened"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

COLUMNS = [
    ("watch_source", "severity_floor"),
    ("foreign_notice", "reference"),
    ("watch_match", "rereview_case_id"),
    ("gap", "pack_version_id"),
]


def upgrade() -> None:
    for table, column in COLUMNS:
        op.add_column(table, sa.Column(column, sa.Text()))


def downgrade() -> None:
    for table, column in reversed(COLUMNS):
        op.drop_column(table, column)
