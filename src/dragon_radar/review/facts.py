"""Market projection reuse limited to a single caller-owned transaction."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import Connection

from dragon_radar.domain.models import SourceGrant
from dragon_radar.readmodels.securities import _Projection, _row
from dragon_radar.storage.models import Snapshot, import_batches, snapshots
from dragon_radar.storage.repository import Forbidden, NotFound, Repository


class TransactionFacts:
    def __init__(self, connection: Connection, market: Repository):
        if not connection.in_transaction():
            raise ValueError("Facts require an active transaction")
        self.connection = connection
        self.transaction = connection.get_transaction()
        self.market = market
        self.snapshots: dict[UUID, Snapshot] = {}
        self.grants: dict[str, SourceGrant] = {}
        self.projections: dict[UUID, _Projection] = {}
        self.rows: dict[tuple[UUID, str], dict] = {}

    def snapshot(self, snapshot_id, security_id=None):
        if (
            not self.transaction.is_active
            or self.connection.get_transaction() is not self.transaction
        ):
            raise ValueError("Facts cannot outlive their transaction")
        snapshot_id = UUID(str(snapshot_id))
        if snapshot_id not in self.snapshots:
            row = (
                self.connection.execute(
                    select(snapshots).where(snapshots.c.id == snapshot_id)
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFound
            source = row["source_id"]
            if source not in self.grants:
                self.grants[source] = self.market._source(self.connection, source)
            archive = (
                self.connection.execute(
                    select(import_batches).where(import_batches.c.id == row["batch_id"])
                )
                .mappings()
                .one()
            )
            self.snapshots[snapshot_id] = self.market._snapshot(row, archive)
        snapshot = self.snapshots[snapshot_id]
        # Source locks prevent concurrent revocation, but time can still expire.
        self.market._authorize(self.grants[snapshot.batch.source_id], snapshot.batch)
        if (
            security_id is not None
            and security_id not in snapshot.batch.expected_security_ids
        ):
            raise NotFound
        return snapshot

    def facts(self, snapshot_id, security_id):
        try:
            snapshot = self.snapshot(snapshot_id, security_id)
        except (Forbidden, NotFound):
            return {"status": "unavailable"}
        result = None
        if security_id is not None:
            key = (snapshot.id, security_id)
            if key not in self.rows:
                if snapshot.id not in self.projections:
                    self.projections[snapshot.id] = _Projection(snapshot.batch)
                self.rows[key] = _row(
                    self.projections[snapshot.id], security_id
                ).model_dump(mode="json")
            result = self.rows[key]
        return {
            "status": "available",
            "snapshot_id": str(snapshot.id),
            "trade_date": snapshot.trade_date.isoformat(),
            "as_of": snapshot.batch.as_of.isoformat(),
            "source_time": snapshot.batch.source_time.isoformat()
            if snapshot.batch.source_time
            else None,
            "provenance": snapshot.batch.provenance,
            "manifest": snapshot.manifest,
            "result": result,
        }
