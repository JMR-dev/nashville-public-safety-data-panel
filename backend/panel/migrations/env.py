"""Migrations run on the caller's configured SQLite connection."""

from alembic import context

from panel.tables import metadata

context.configure(connection=context.config.attributes["connection"], target_metadata=metadata)
with context.begin_transaction():
    context.run_migrations()
