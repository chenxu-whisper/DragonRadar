"""Local operator output license registration; current rights never cached."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import insert, select, text

from dragon_radar.public_daily_ai.models import (
    AIOutputEntitlement,
    Reference,
    digest,
    utc_time,
)
from dragon_radar.public_daily_ai.tables import (
    ai_output_entitlement_revocations as revocations,
)
from dragon_radar.public_daily_ai.tables import (
    ai_output_entitlements as entitlements,
)
from dragon_radar.storage.repository import Conflict, Forbidden, NotFound


def lock(connection, key):
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
    )


class OutputEntitlementRepository:
    def __init__(self, database, *, clock=None):
        self.database = database
        raw_clock = clock or (lambda: datetime.now(UTC))
        self.clock = lambda: utc_time(raw_clock())

    def register(self, document):
        document = AIOutputEntitlement.model_validate(document)
        data = document.model_dump(mode="json")
        with self.database.engine.begin() as connection:
            lock(connection, "ai-output:" + document.reference)
            old = (
                connection.execute(
                    select(entitlements).where(
                        entitlements.c.reference == document.reference,
                    )
                )
                .mappings()
                .first()
            )
            if old:
                if old["content_hash"] != digest(data):
                    raise Conflict("Entitlement reference is immutable")
                return document.reference
            connection.execute(
                insert(entitlements).values(
                    reference=document.reference,
                    document=data,
                    content_hash=digest(data),
                    registered_at=self.clock(),
                )
            )
        return document.reference

    def revoke(self, reference, *, key, reason_reference):
        from pydantic import TypeAdapter

        for value in (reference, key, reason_reference):
            TypeAdapter(Reference).validate_python(value)
        with self.database.engine.begin() as connection:
            lock(connection, "ai-output-revoke-key:" + key)
            lock(connection, "ai-output:" + reference)
            if not connection.scalar(
                select(entitlements.c.reference).where(
                    entitlements.c.reference == reference
                )
            ):
                raise NotFound("Output entitlement not found")
            old = (
                connection.execute(
                    select(revocations).where(
                        revocations.c.idempotency_key == key,
                    )
                )
                .mappings()
                .first()
            )
            if old:
                if (old["reference"], old["reason_reference"]) != (
                    reference,
                    reason_reference,
                ):
                    raise Conflict("Output revocation key conflict")
                return
            connection.execute(
                insert(revocations).values(
                    id=uuid4(),
                    reference=reference,
                    idempotency_key=key,
                    reason_reference=reason_reference,
                    revoked_at=self.clock(),
                )
            )

    def authorize(
        self,
        connection,
        reference,
        *,
        provider,
        model_version,
        frozen=None,
        public=True,
    ):
        lock(connection, "ai-output:" + reference)
        row = (
            connection.execute(
                select(entitlements).where(
                    entitlements.c.reference == reference,
                )
            )
            .mappings()
            .first()
        )
        if not row or connection.scalar(
            select(revocations.c.id)
            .where(revocations.c.reference == reference)
            .limit(1)
        ):
            raise Forbidden("Output rights unavailable")
        current = AIOutputEntitlement.model_validate(row["document"])
        values = (
            [current]
            if frozen is None
            else [current, AIOutputEntitlement.model_validate(frozen)]
        )
        if frozen is not None and current.model_dump(mode="json") != frozen:
            raise Forbidden("Output rights mismatch")
        if not all(
            v.allows(
                self.clock(),
                provider=provider,
                model_version=model_version,
                public=public,
            )
            for v in values
        ):
            raise Forbidden("Output rights denied")
        return current.model_dump(mode="json")
