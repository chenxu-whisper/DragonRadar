"""Owner schema mappings share the research database metadata."""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from dragon_radar.storage.models import metadata

owner_principals = Table(
    "owner_principals",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("local_key", Text, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("local_key IS NULL OR local_key = 'local'"),
)

owner_pairings = Table(
    "owner_pairings",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "owner_id",
        PGUUID(as_uuid=True),
        ForeignKey("owner_principals.id"),
        nullable=False,
    ),
    Column("code_hash", String(64), nullable=False, unique=True),
    Column("origin", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("consumed_at", DateTime(timezone=True)),
    Column("revoked_at", DateTime(timezone=True)),
    Index("owner_pairings_owner", "owner_id"),
)

owner_sessions = Table(
    "owner_sessions",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "owner_id",
        PGUUID(as_uuid=True),
        ForeignKey("owner_principals.id"),
        nullable=False,
    ),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("csrf_hash", String(64), nullable=False),
    Column("origin", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True)),
    Index("owner_sessions_owner", "owner_id"),
)

owner_pairing_limits = Table(
    "owner_pairing_limits",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("window_started_at", DateTime(timezone=True)),
    Column("failures", Integer, nullable=False),
    CheckConstraint("id = 1"),
    CheckConstraint("failures >= 0 AND failures <= 5"),
)


@dataclass(frozen=True)
class OwnerIdentity:
    owner_id: UUID
    session_id: UUID
    expires_at: datetime
    origin: str


@dataclass(frozen=True)
class IssuedSession(OwnerIdentity):
    token: str = field(repr=False)
    csrf: str = field(repr=False)
