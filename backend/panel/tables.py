"""Source-preserving police call storage with separately kept local metadata.

Upstream attribute names and types are stored exactly as published. Every attribute, including
ones this schema does not know yet, also survives verbatim in ``raw``. Local bookkeeping uses
lower-case column names. Provenance, schema snapshots, ingestion timestamps, checkpoints, and
source status are each kept in their own place.
"""

from typing import Any, Final

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
)
from sqlalchemy.types import TypeEngine

metadata = MetaData()

# Esri field types, verified against the active layer's metadata on 2026-09-19.
SOURCE_FIELDS: Final[dict[str, str]] = {
    "OBJECTID": "esriFieldTypeOID",
    "Event_Number": "esriFieldTypeString",
    "Complaint_Number": "esriFieldTypeDouble",
    "Tencode": "esriFieldTypeInteger",
    "Tencode_Description": "esriFieldTypeString",
    "Tencode_Suffix": "esriFieldTypeString",
    "Tencode_Suffix_Description": "esriFieldTypeString",
    "Disposition_Code": "esriFieldTypeString",
    "Disposition_Description": "esriFieldTypeString",
    "Block": "esriFieldTypeString",
    "Street_Name": "esriFieldTypeString",
    "Unit_Dispatched": "esriFieldTypeString",
    "Shift": "esriFieldTypeString",
    "Sector": "esriFieldTypeString",
    "Mapped_Location": "esriFieldTypeString",
    "POINT_X": "esriFieldTypeDouble",
    "POINT_Y": "esriFieldTypeDouble",
    "ZONE_": "esriFieldTypeString",
    "Latitude": "esriFieldTypeDouble",
    "Longitude": "esriFieldTypeDouble",
    "RPA": "esriFieldTypeString",
    "Call_Received": "esriFieldTypeDate",
}

# Dates stay as the published epoch milliseconds, so storage is lossless.
SQL_TYPES: Final[dict[str, TypeEngine[Any]]] = {
    "esriFieldTypeOID": Integer(),
    "esriFieldTypeString": String(),
    "esriFieldTypeDouble": Float(),
    "esriFieldTypeInteger": Integer(),
    "esriFieldTypeDate": Integer(),
}

ATTRIBUTE_FIELDS: Final[tuple[str, ...]] = tuple(
    name for name in SOURCE_FIELDS if name != "OBJECTID"
)

generations = Table(
    "source_generations",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source", String, nullable=False),
    Column("url", String, nullable=False),
    Column("service_item_id", String),
    Column("layer_name", String),
    Column("reason", String, nullable=False),
    Column("created_at", Integer, nullable=False),
    Column("active", Boolean, nullable=False),
    Column("retired_at", Integer),
    Column("boundary", Integer),
    Column("backfill_completed_at", Integer),
)
Index("generations_source_active", generations.c.source, generations.c.active)

schema_snapshots = Table(
    "schema_snapshots",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("generation", Integer, ForeignKey("source_generations.id"), nullable=False),
    Column("captured_at", Integer, nullable=False),
    Column("fingerprint", String, nullable=False),
    Column("fields", JSON, nullable=False),
    Column("compatible", Boolean, nullable=False),
    Column("problems", JSON, nullable=False),
)
Index("schema_snapshots_generation", schema_snapshots.c.generation, schema_snapshots.c.id)

calls = Table(
    "police_calls",
    metadata,
    Column("generation", Integer, ForeignKey("source_generations.id"), primary_key=True),
    Column("OBJECTID", Integer, primary_key=True),
    *(Column(name, SQL_TYPES[SOURCE_FIELDS[name]]) for name in ATTRIBUTE_FIELDS),
    Column("raw", JSON, nullable=False),
    Column("fingerprint", String, nullable=False),
    Column("first_seen_at", Integer, nullable=False),
    Column("last_seen_at", Integer, nullable=False),
    Column("last_changed_at", Integer, nullable=False),
    Column("first_seen_version", Integer, nullable=False),
    Column("changed_version", Integer, nullable=False),
    Column("source_present", Boolean, nullable=False),
    Column("removed_at", Integer),
)
Index("calls_received", calls.c.Call_Received, calls.c.OBJECTID, calls.c.generation)
Index("calls_zone", calls.c.ZONE_, calls.c.Call_Received)
Index("calls_sector", calls.c.Sector, calls.c.Call_Received)
Index("calls_type", calls.c.Tencode_Description, calls.c.Call_Received)
Index("calls_disposition", calls.c.Disposition_Description, calls.c.Call_Received)

checkpoints = Table(
    "checkpoints",
    metadata,
    Column("generation", Integer, ForeignKey("source_generations.id"), primary_key=True),
    Column("name", String, primary_key=True),
    Column("lower", Integer),
    Column("upper", Integer),
    Column("cursor", Integer, nullable=False),
    Column("completed_at", Integer),
    Column("updated_at", Integer, nullable=False),
)

source_status = Table(
    "source_status",
    metadata,
    Column("source", String, primary_key=True),
    Column("generation", Integer, ForeignKey("source_generations.id")),
    Column("state", String, nullable=False),
    Column("detail", String),
    Column("last_poll_at", Integer),
    Column("last_change_at", Integer),
    Column("upstream_edit_at", Integer),
    Column("degraded_since", Integer),
    Column("retry_at", Integer),
    Column("window_reconciled_at", Integer),
    Column("full_reconciled_at", Integer),
    Column("updated_at", Integer, nullable=False),
)

state = Table(
    "state",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", JSON, nullable=False),
)
