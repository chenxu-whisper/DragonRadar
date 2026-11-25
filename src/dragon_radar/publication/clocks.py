"""Private verified evidence and immutable daily clock projections."""

import hashlib
import json
from datetime import datetime

from sqlalchemy import insert, select

from dragon_radar.domain.clocks import (
    CLOCK_NAMES,
    DAILY_DOMAINS,
    DomainClockArchive,
    InputClockEvidence,
    InputClockReference,
    aggregate_clock,
    domain_cells,
    not_applicable,
    scalar_clock,
    utc,
    validate_publication_time,
)
from dragon_radar.http.errors import DataUnavailable
from dragon_radar.publication.tables import input_clock_evidence
from dragon_radar.storage.repository import Conflict


def verify_input_evidence(
    evidence: InputClockEvidence,
    object_bytes: bytes,
    evidence_bytes: bytes,
    *,
    verifier,
) -> InputClockEvidence:
    """Verifier must resolve independent trusted acquisition/source attestations.

    A sidecar hash is integrity, not receipt-time authority. The injected verifier
    validates actual private evidence under the collector's trust policy.
    """
    evidence = InputClockEvidence.model_validate(evidence.model_dump())
    if (
        hashlib.sha256(object_bytes).hexdigest() != evidence.input_sha256
        or hashlib.sha256(evidence_bytes).hexdigest() != evidence.evidence_sha256
    ):
        raise ValueError("Input/evidence hash mismatch")
    expected = evidence.model_dump(mode="json", exclude={"evidence_sha256"})
    try:
        # Normalize attestation timestamps through the same strict UTC contract.
        supplied = json.loads(evidence_bytes)
        bound = InputClockEvidence(
            **supplied, evidence_sha256=evidence.evidence_sha256
        ).model_dump(mode="json", exclude={"evidence_sha256"})
    except (ValueError, TypeError):
        raise ValueError("Invalid evidence binding") from None
    if bound != expected:
        raise ValueError("Evidence source/object/timestamp binding mismatch")
    if verifier(evidence, object_bytes, evidence_bytes) is not True:
        raise ValueError("Input evidence is not independently trusted")
    return evidence


class InputClockRepository:
    def append(
        self, connection, evidence, *, object_bytes, evidence_bytes, verifier
    ) -> InputClockEvidence:
        checked = verify_input_evidence(
            evidence, object_bytes, evidence_bytes, verifier=verifier
        )
        # Serialize exact-key writers without changing the first acquisition.
        from sqlalchemy import text

        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": "input-clock:" + json.dumps(checked.key())},
        )
        old = self.get(connection, checked.key())
        if old is not None:
            if old != checked:
                raise Conflict("Input clock revision is immutable")
            return old
        connection.execute(insert(input_clock_evidence).values(**checked.model_dump()))
        return checked

    @staticmethod
    def get(connection, key) -> InputClockEvidence | None:
        query = select(input_clock_evidence).where(
            *(
                column == value
                for column, value in zip(
                    (
                        input_clock_evidence.c.source_id,
                        input_clock_evidence.c.archive_type,
                        input_clock_evidence.c.archive_id,
                        input_clock_evidence.c.revision_key,
                    ),
                    key,
                    strict=True,
                )
            )
        )
        row = connection.execute(query).mappings().first()
        return InputClockEvidence.model_validate(dict(row)) if row is not None else None


def input_reference(
    connection,
    *,
    source_id,
    archive_type,
    archive_id,
    revision_key,
    source_version,
    input_sha256,
) -> InputClockReference:
    key = source_id, archive_type, str(archive_id), str(revision_key)
    observed = InputClockRepository.get(connection, key)
    if observed is not None:
        if (
            observed.source_version != source_version
            or observed.input_sha256 != input_sha256
        ):
            raise ValueError("Archived clock input hash/source mismatch")
        return observed
    return InputClockReference(
        source_id=source_id,
        archive_type=archive_type,
        archive_id=str(archive_id),
        revision_key=str(revision_key),
        source_version=source_version,
        input_sha256=input_sha256,
        evidence_sha256=None,
        source_event_at=None,
        provider_published_at=None,
        first_received_at=None,
    )


def archive_domain(
    *, domain, input_id, as_of, status, inputs, computed_at
) -> DomainClockArchive:
    return DomainClockArchive(
        domain=domain,
        input_id=input_id,
        as_of=as_of,
        status=status,
        inputs=tuple(sorted(inputs, key=lambda item: item.key())),
        computed_at=computed_at,
    )


def resolve_domain_inputs(connection, dependencies, *, regulation_context=None):
    """Resolve exact immutable archives; never use latest or creation timestamps."""
    from dragon_radar.news.repository import _revision
    from dragon_radar.news.tables import news_article_revisions
    from dragon_radar.storage.models import import_batches
    from dragon_radar.topics.tables import topic_import_batches

    inputs = []
    for source_id, archive_type, archive_id in sorted(set(dependencies), key=str):
        if archive_type in ("market_import_batch", "topic_import_batch"):
            table = (
                import_batches
                if archive_type == "market_import_batch"
                else topic_import_batches
            )
            row = (
                connection.execute(select(table).where(table.c.id == archive_id))
                .mappings()
                .one()
            )
            body = json.loads(row["canonical_json"])
            if body["source_id"] != source_id or row["source_id"] != source_id:
                raise ValueError("Clock input source mismatch")
            digest = hashlib.sha256(row["canonical_json"].encode()).hexdigest()
            inputs.append(
                input_reference(
                    connection,
                    source_id=source_id,
                    archive_type=archive_type,
                    archive_id=archive_id,
                    revision_key="1",
                    source_version=body["source_version"],
                    input_sha256=digest,
                )
            )
        elif archive_type == "news_article_revision":
            row = (
                connection.execute(
                    select(news_article_revisions).where(
                        news_article_revisions.c.id == archive_id
                    )
                )
                .mappings()
                .one()
            )
            if row["source_id"] != source_id:
                raise ValueError("Clock news revision source mismatch")
            body = _revision(row).model_dump(mode="json")
            digest = hashlib.sha256(
                json.dumps(
                    body, sort_keys=True, separators=(",", ":"), ensure_ascii=True
                ).encode("ascii")
            ).hexdigest()
            item = input_reference(
                connection,
                source_id=source_id,
                archive_type=archive_type,
                archive_id=archive_id,
                revision_key=str(row["revision"]),
                source_version=row["source_version"],
                input_sha256=digest,
            )
            if item.evidence_sha256 is None:
                # The archived revision itself resolves this evidence digest.
                item = InputClockReference(
                    **{
                        **item.model_dump(),
                        "evidence_sha256": digest,
                        "provider_published_at": row["published_at"],
                        "first_received_at": row["received_at"],
                    }
                )
            inputs.append(item)
        elif archive_type == "regulation_event_snapshot":
            context = regulation_context
            if context is None or context["snapshot"].id != archive_id:
                raise ValueError("Clock regulation context mismatch")
            events = [
                event for event in context["events"] if event.source_id == source_id
            ]
            versions = {event.source_version for event in events}
            if context["snapshot"].event_source_id == source_id:
                versions.add(context["snapshot"].event_source_version)
            if len(versions) != 1 or None in versions:
                raise ValueError("Clock regulation source version unavailable")
            body = {
                "snapshot": context["snapshot"].model_dump(mode="json"),
                "events": [event.model_dump(mode="json") for event in events],
            }
            digest = hashlib.sha256(
                json.dumps(
                    body, sort_keys=True, separators=(",", ":"), ensure_ascii=True
                ).encode("ascii")
            ).hexdigest()
            inputs.append(
                input_reference(
                    connection,
                    source_id=source_id,
                    archive_type=archive_type,
                    archive_id=archive_id,
                    revision_key="1",
                    source_version=next(iter(versions)),
                    input_sha256=digest,
                )
            )
        else:
            raise ValueError("Unsupported clock archive type")
    return tuple(inputs)


def publication_lineage(publication, results, *, legacy_cutoffs=None) -> dict:
    results = {result.domain: result for result in results}
    if set(results) != set(DAILY_DOMAINS):
        raise DataUnavailable
    try:
        cutoffs = {
            domain: utc(value) if value is not None else None
            for domain, value in (legacy_cutoffs or {}).items()
        }
    except (ValueError, TypeError, AttributeError):
        raise DataUnavailable from None
    domains = {}
    for domain in DAILY_DOMAINS:
        result = results[domain]
        archive = result.clock_lineage
        if archive is not None:
            if (
                archive.domain != domain
                or archive.input_id != result.input_id
                or archive.status != result.status
            ):
                raise DataUnavailable
            try:
                validate_publication_time(
                    (archive.computed_at,), publication.published_at
                )
                cells = domain_cells(archive, published_at=publication.published_at)
            except ValueError:
                raise DataUnavailable from None
            as_of = archive.as_of
        else:
            reason = (
                "legacy_clock_unavailable"
                if result.status == "ready"
                else "domain_" + result.status
            )
            cells = {
                name: scalar_clock(None, reason=reason) for name in CLOCK_NAMES[:4]
            }
            cells["snapshot_published_at"] = scalar_clock(publication.published_at)
            cells["page_updated_at"] = not_applicable("browser_clock")
            as_of = cutoffs.get(domain)
            # These are actual archived revision clocks, not article-global times.
            if domain == "news" and result.status == "ready":
                try:
                    articles = result.payload["articles"]
                    for name, field in (
                        ("provider_published_at", "published_at"),
                        ("first_received_at", "received_at"),
                    ):
                        cells[name] = aggregate_clock(
                            (
                                utc(datetime.fromisoformat(article[field]))
                                if article.get(field)
                                else None
                                for article in articles
                            ),
                            reason="legacy_clock_unavailable",
                        )
                except (KeyError, TypeError, ValueError):
                    raise DataUnavailable from None
        domains[domain] = {
            "status": result.status,
            "input_id": str(result.input_id) if result.input_id is not None else None,
            "as_of": as_of.isoformat() if as_of is not None else None,
            "clocks": {
                name: cell.model_dump(mode="json") for name, cell in cells.items()
            },
        }
    return {
        "schema": "dragon-radar.publication-clocks/v1",
        "snapshot_id": str(publication.id),
        "trade_date": publication.trade_date.isoformat(),
        "domains": domains,
    }


def source_lineage(
    *, identity, as_of, events, receipts, computed_at=None, providers=()
) -> dict:
    cells = {
        "source_event_at": aggregate_clock(events, reason="input_clock_unavailable"),
        "provider_published_at": aggregate_clock(
            providers, reason="input_clock_unavailable"
        ),
        "first_received_at": aggregate_clock(
            receipts, reason="input_clock_unavailable"
        ),
        "computed_at": scalar_clock(computed_at),
        "snapshot_published_at": not_applicable("domain_clock_not_applicable"),
        "page_updated_at": not_applicable("browser_clock"),
    }
    return {
        "schema": "dragon-radar.source-clocks/v1",
        "identity": identity,
        "as_of": utc(as_of).isoformat(),
        "clocks": {name: cell.model_dump(mode="json") for name, cell in cells.items()},
    }
