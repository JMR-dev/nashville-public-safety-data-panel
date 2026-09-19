"""Initial upstream schema and separate local metadata."""

from sqlalchemy import JSON, Boolean, Column, Float, Index, Integer, MetaData, String, Table

metadata = MetaData()
TEXT_FIELDS = (
    "Event_Number", "Tencode_Description", "Tencode_Suffix", "Tencode_Suffix_Description",
    "Disposition_Code", "Disposition_Description", "Block", "Street_Name", "Unit_Dispatched",
    "Shift", "Sector", "Mapped_Location", "ZONE_", "RPA",
)
REAL_FIELDS = ("Complaint_Number", "POINT_X", "POINT_Y", "Latitude", "Longitude")
INTEGER_FIELDS = ("Tencode", "Call_Received")

generations = Table(
    "source_generations", metadata,
    Column("id", Integer, primary_key=True), Column("source", String, nullable=False),
    Column("upper", Integer, nullable=False), Column("active", Boolean, nullable=False),
    Column("schema", JSON, nullable=False),
)
calls = Table(
    "police_calls", metadata,
    Column("generation", Integer, primary_key=True), Column("OBJECTID", Integer, primary_key=True),
    *(Column(name, String) for name in TEXT_FIELDS),
    *(Column(name, Float) for name in REAL_FIELDS),
    *(Column(name, Integer) for name in INTEGER_FIELDS),
    Column("raw", JSON, nullable=False), Column("fingerprint", String, nullable=False),
    Column("observed_at", Integer, nullable=False), Column("seen_run", String, nullable=False),
    Column("source_present", Boolean, nullable=False),
)
Index("calls_received", calls.c.Call_Received, calls.c.OBJECTID, calls.c.generation)
Index("calls_zone", calls.c.ZONE_, calls.c.Call_Received)
Index("calls_sector", calls.c.Sector, calls.c.Call_Received)
Index("calls_type", calls.c.Tencode_Description, calls.c.Call_Received)
checkpoints = Table(
    "checkpoints", metadata,
    Column("generation", Integer, primary_key=True), Column("name", String, primary_key=True),
    Column("cursor", Integer, nullable=False),
)
state = Table("state", metadata, Column("key", String, primary_key=True), Column("value", JSON))
