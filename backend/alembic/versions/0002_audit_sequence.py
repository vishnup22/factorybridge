"""audit_events.seq: monotonic total order for audit reconstruction

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD COLUMN backfills via table rewrite; row-level append-only triggers do not fire.
    op.add_column(
        "audit_events",
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
    )
    op.create_unique_constraint(op.f("uq_audit_events_seq"), "audit_events", ["seq"])


def downgrade() -> None:
    op.drop_constraint(op.f("uq_audit_events_seq"), "audit_events", type_="unique")
    op.drop_column("audit_events", "seq")
