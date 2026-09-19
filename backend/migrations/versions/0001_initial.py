"""Source-preserving police calls and ingestion metadata."""

from alembic import op

from panel.tables import metadata

revision = "0001"
down_revision = None


def upgrade() -> None:
    metadata.create_all(op.get_bind())


def downgrade() -> None:
    metadata.drop_all(op.get_bind())
