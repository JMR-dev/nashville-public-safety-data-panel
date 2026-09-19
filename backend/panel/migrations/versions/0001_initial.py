"""Source-preserving police calls and separately kept ingestion metadata.

Revisions spell out their DDL instead of calling ``metadata.create_all()``: a revision must keep
meaning the same schema after ``panel.tables`` changes.
"""

from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None

UPSTREAM_COLUMNS: tuple[tuple[str, sa.types.TypeEngine[Any]], ...] = (
    ("Event_Number", sa.String()),
    ("Complaint_Number", sa.Float()),
    ("Tencode", sa.Integer()),
    ("Tencode_Description", sa.String()),
    ("Tencode_Suffix", sa.String()),
    ("Tencode_Suffix_Description", sa.String()),
    ("Disposition_Code", sa.String()),
    ("Disposition_Description", sa.String()),
    ("Block", sa.String()),
    ("Street_Name", sa.String()),
    ("Unit_Dispatched", sa.String()),
    ("Shift", sa.String()),
    ("Sector", sa.String()),
    ("Mapped_Location", sa.String()),
    ("POINT_X", sa.Float()),
    ("POINT_Y", sa.Float()),
    ("ZONE_", sa.String()),
    ("Latitude", sa.Float()),
    ("Longitude", sa.Float()),
    ("RPA", sa.String()),
    ("Call_Received", sa.Integer()),
)


def upgrade() -> None:
    op.create_table(
        "source_generations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("service_item_id", sa.String()),
        sa.Column("layer_name", sa.String()),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("retired_at", sa.Integer()),
        sa.Column("boundary", sa.Integer()),
        sa.Column("backfill_completed_at", sa.Integer()),
    )
    op.create_index("generations_source_active", "source_generations", ["source", "active"])

    op.create_table(
        "schema_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "generation", sa.Integer(), sa.ForeignKey("source_generations.id"), nullable=False
        ),
        sa.Column("captured_at", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column("compatible", sa.Boolean(), nullable=False),
        sa.Column("problems", sa.JSON(), nullable=False),
    )
    op.create_index("schema_snapshots_generation", "schema_snapshots", ["generation", "id"])

    op.create_table(
        "police_calls",
        sa.Column(
            "generation", sa.Integer(), sa.ForeignKey("source_generations.id"), primary_key=True
        ),
        sa.Column("OBJECTID", sa.Integer(), primary_key=True),
        *(sa.Column(name, column_type) for name, column_type in UPSTREAM_COLUMNS),
        sa.Column("extra", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("first_seen_at", sa.Integer(), nullable=False),
        sa.Column("last_seen_at", sa.Integer(), nullable=False),
        sa.Column("last_changed_at", sa.Integer(), nullable=False),
        sa.Column("first_seen_version", sa.Integer(), nullable=False),
        sa.Column("changed_version", sa.Integer(), nullable=False),
        sa.Column("source_present", sa.Boolean(), nullable=False),
        sa.Column("removed_at", sa.Integer()),
    )
    op.create_index("calls_received", "police_calls", ["Call_Received", "OBJECTID", "generation"])
    op.create_index("calls_zone", "police_calls", ["ZONE_", "Call_Received"])
    op.create_index("calls_sector", "police_calls", ["Sector", "Call_Received"])
    op.create_index("calls_type", "police_calls", ["Tencode_Description", "Call_Received"])
    op.create_index(
        "calls_disposition", "police_calls", ["Disposition_Description", "Call_Received"]
    )

    op.create_table(
        "checkpoints",
        sa.Column(
            "generation", sa.Integer(), sa.ForeignKey("source_generations.id"), primary_key=True
        ),
        sa.Column("name", sa.String(), primary_key=True),
        sa.Column("lower", sa.Integer()),
        sa.Column("upper", sa.Integer()),
        sa.Column("cursor", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.Integer()),
        sa.Column("updated_at", sa.Integer(), nullable=False),
    )

    op.create_table(
        "source_status",
        sa.Column("source", sa.String(), primary_key=True),
        sa.Column("generation", sa.Integer(), sa.ForeignKey("source_generations.id")),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("detail", sa.String()),
        sa.Column("last_poll_at", sa.Integer()),
        sa.Column("last_change_at", sa.Integer()),
        sa.Column("upstream_edit_at", sa.Integer()),
        sa.Column("degraded_since", sa.Integer()),
        sa.Column("retry_at", sa.Integer()),
        sa.Column("window_reconciled_at", sa.Integer()),
        sa.Column("full_reconciled_at", sa.Integer()),
        sa.Column("updated_at", sa.Integer(), nullable=False),
    )

    op.create_table(
        "state",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    for table in (
        "state",
        "source_status",
        "checkpoints",
        "police_calls",
        "schema_snapshots",
        "source_generations",
    ):
        op.drop_table(table)
