"""Transactional owner-scoped research; market facts are never copied to text."""

import hashlib
import json
import re
from datetime import UTC, date, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import insert, select, update

from dragon_radar.review.facts import TransactionFacts
from dragon_radar.review.models import NoteContent, validate_confirmation
from dragon_radar.review.tables import (
    owner_commands,
    private_audit_events,
)
from dragon_radar.review.tables import (
    review_note_revisions as revisions,
)
from dragon_radar.review.tables import (
    review_notes as notes,
)
from dragon_radar.review.tables import (
    review_verifications as verifications,
)
from dragon_radar.review.tables import (
    watch_entries as watch,
)
from dragon_radar.review.templates import TEMPLATE_VERSION, template_content
from dragon_radar.storage.models import snapshots
from dragon_radar.storage.repository import Conflict, NotFound


class VersionConflict(Conflict):
    def __init__(self, current):
        super().__init__("Resource version conflict")
        self.current = current


def _id(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid record ID") from None


def _json(value):
    return json.loads(json.dumps(value, default=str, sort_keys=True, ensure_ascii=True))


def _page(page, page_size):
    if type(page) is not int or not 1 <= page <= 100000:
        raise ValueError("Invalid page")
    if type(page_size) is not int or page_size not in (20, 50, 100):
        raise ValueError("Invalid page size")


def _version(value):
    if type(value) is not int or not 1 <= value <= 2147483647:
        raise ValueError("Invalid revision number")
    return value


def _security(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", value):
        raise ValueError("Invalid security ID")
    return value


class ReviewRepository:
    def __init__(self, database, owner_repository, market_repository, clock=None):
        self.database = database
        self.owners = owner_repository
        self.market = market_repository
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self):
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Review clock must be aware")
        return value.astimezone(UTC)

    def _snapshot(self, connection, snapshot_id, security_id=None):
        return TransactionFacts(connection, self.market).snapshot(
            _id(snapshot_id), security_id
        )

    def _lock_sources(self, connection, snapshot_ids):
        ids = {_id(value) for value in snapshot_ids if value is not None}
        if not ids:
            return
        sources = (
            connection.execute(
                select(snapshots.c.source_id).where(snapshots.c.id.in_(ids)).distinct()
            )
            .scalars()
            .all()
        )
        for source_id in sorted(sources):
            self.market._source(connection, source_id)

    def _command_sources(self, connection, owner, payload, previous):
        ids = [payload.get("snapshot_id")]
        if payload.get("security_id") is not None:
            ids.extend(
                connection.execute(
                    select(watch.c.snapshot_id).where(
                        watch.c.owner_id == owner,
                        watch.c.security_id == payload["security_id"],
                    )
                ).scalars()
            )
        for record_id in (payload.get("id"), previous["id"] if previous else None):
            if record_id is None:
                continue
            for table in (notes, watch):
                ids.extend(
                    connection.execute(
                        select(table.c.snapshot_id).where(
                            table.c.owner_id == owner, table.c.id == _id(record_id)
                        )
                    ).scalars()
                )
        self._lock_sources(connection, ids)

    def _owned(self, connection, table, owner, record_id):
        row = (
            connection.execute(
                select(table).where(
                    table.c.id == _id(record_id), table.c.owner_id == owner
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound
        return row

    def _facts(self, connection, snapshot_id, security_id):
        return TransactionFacts(connection, self.market).facts(snapshot_id, security_id)

    @staticmethod
    def _note_result(head, row, facts):
        return _json(
            {
                **dict(head),
                "version": row["version"],
                "state": row["state"],
                "revision_id": row["id"],
                "saved_at": row["saved_at"],
                "content": row["content"],
                "facts": facts,
            }
        )

    def _note(self, connection, owner, record_id, version=None):
        if version is not None:
            _version(version)
        head = self._owned(connection, notes, owner, record_id)
        row = (
            connection.execute(
                select(revisions).where(
                    revisions.c.owner_id == owner,
                    revisions.c.note_id == head["id"],
                    revisions.c.version
                    == (head["version"] if version is None else version),
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound
        return self._note_result(
            head,
            row,
            self._facts(connection, head["snapshot_id"], head["security_id"]),
        )

    def _resolve(self, connection, owner, reference):
        if reference["kind"] == "note":
            return self._note(connection, owner, reference["id"], reference["version"])
        table = watch if reference["kind"] == "watch" else verifications
        row = dict(self._owned(connection, table, owner, reference["id"]))
        if table is watch:
            row["facts"] = self._facts(
                connection, row["snapshot_id"], row["security_id"]
            )
            if "version" in reference:
                row["committed"] = {
                    "id": reference["id"],
                    "version": reference["version"],
                }
        return _json(row)

    def _command(self, token, csrf, key, action, payload, operation):
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", key):
            raise ValueError("Invalid idempotency key")
        if not isinstance(csrf, str) or not csrf:
            from dragon_radar.owner import CsrfForbidden

            raise CsrfForbidden
        digest = hashlib.sha256(
            json.dumps(
                _json({"action": action, "payload": payload}),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode()
        ).hexdigest()
        with self.database.engine.begin() as connection:
            identity = self.owners.authorize_connection(connection, token, csrf)
            owner = identity.owner_id
            previous = (
                connection.execute(
                    select(owner_commands).where(
                        owner_commands.c.owner_id == owner,
                        owner_commands.c.key == key,
                    )
                )
                .mappings()
                .first()
            )
            if previous:
                if previous["digest"] != digest:
                    raise Conflict("Idempotency key conflict")
                self._command_sources(connection, owner, payload, previous["result"])
                return self._resolve(connection, owner, previous["result"])
            self._command_sources(connection, owner, payload, None)
            result = operation(connection, owner)
            now = self._now()
            connection.execute(
                insert(owner_commands).values(
                    owner_id=owner,
                    key=key,
                    digest=digest,
                    result=_json(result),
                    created_at=now,
                )
            )
            connection.execute(
                insert(private_audit_events).values(
                    id=uuid4(),
                    owner_id=owner,
                    action=action,
                    object_id=_id(result["id"]),
                    version=result.get("version"),
                    occurred_at=now,
                )
            )
            return self._resolve(connection, owner, result)

    def _expected(self, connection, owner, table, row, expected):
        _version(expected)
        if expected == 2147483647:
            raise ValueError("Revision limit reached")
        if row["version"] != expected:
            current = (
                self._note(connection, owner, row["id"])
                if table is notes
                else _json(dict(row))
            )
            raise VersionConflict(current)

    def add_watch(self, token, csrf, *, key, snapshot_id, security_id):
        snapshot_id = _id(snapshot_id)
        security_id = _security(security_id)

        def operation(connection, owner):
            self._snapshot(connection, snapshot_id, security_id)
            row = (
                connection.execute(
                    select(watch).where(
                        watch.c.owner_id == owner,
                        watch.c.security_id == security_id,
                    )
                )
                .mappings()
                .first()
            )
            if row:
                if not row["active"]:
                    raise VersionConflict(_json(dict(row)))
                return {"kind": "watch", "id": row["id"], "version": row["version"]}
            now, record_id = self._now(), uuid4()
            connection.execute(
                insert(watch).values(
                    id=record_id,
                    owner_id=owner,
                    security_id=security_id,
                    snapshot_id=snapshot_id,
                    active=True,
                    version=1,
                    added_at=now,
                    updated_at=now,
                )
            )
            return {"kind": "watch", "id": record_id, "version": 1}

        return self._command(
            token,
            csrf,
            key,
            "watch.add",
            locals_payload(snapshot_id=snapshot_id, security_id=security_id),
            operation,
        )

    def set_watch(self, token, csrf, record_id, *, key, expected, active):
        record_id = _id(record_id)
        if type(active) is not bool:
            raise ValueError("Invalid active state")

        def operation(connection, owner):
            row = self._owned(connection, watch, owner, record_id)
            self._expected(connection, owner, watch, row, expected)
            version = expected + 1
            connection.execute(
                update(watch)
                .where(
                    watch.c.id == record_id,
                    watch.c.owner_id == owner,
                    watch.c.version == expected,
                )
                .values(active=active, version=version, updated_at=self._now())
            )
            return {"kind": "watch", "id": record_id, "version": version}

        return self._command(
            token,
            csrf,
            key,
            "watch.set",
            locals_payload(id=record_id, expected=expected, active=active),
            operation,
        )

    def _new_note(
        self,
        connection,
        owner,
        snapshot_id,
        security_id,
        content,
        kind="note",
        template_version=None,
        derived=None,
        inherited_trade_date=None,
    ):
        snapshot_id = _id(snapshot_id)
        trade_date = inherited_trade_date
        if trade_date is None:
            trade_date = self._snapshot(connection, snapshot_id, security_id).trade_date
        now, record_id = self._now(), uuid4()
        connection.execute(
            insert(notes).values(
                id=record_id,
                owner_id=owner,
                snapshot_id=snapshot_id,
                trade_date=trade_date,
                security_id=security_id,
                kind=kind,
                version=1,
                state="draft",
                derived_from_revision_id=derived,
                template_version=template_version,
                created_at=now,
                updated_at=now,
            )
        )
        self._revision(connection, owner, record_id, 1, "draft", content)
        return {"kind": "note", "id": record_id, "version": 1}

    def _revision(self, connection, owner, record_id, version, state, content):
        connection.execute(
            insert(revisions).values(
                id=uuid4(),
                owner_id=owner,
                note_id=record_id,
                version=version,
                state=state,
                content=content.model_dump(mode="json"),
                saved_at=self._now(),
            )
        )

    def create_note(self, token, csrf, *, key, snapshot_id, content, security_id=None):
        content = NoteContent.model_validate(content.model_dump())
        snapshot_id = _id(snapshot_id)
        if security_id is not None:
            security_id = _security(security_id)
        return self._command(
            token,
            csrf,
            key,
            "note.create",
            locals_payload(
                snapshot_id=snapshot_id,
                security_id=security_id,
                content=content.model_dump(mode="json"),
            ),
            lambda connection, owner: self._new_note(
                connection, owner, snapshot_id, security_id, content
            ),
        )

    def save_note(self, token, csrf, record_id, *, key, expected, content):
        content = NoteContent.model_validate(content.model_dump())
        return self._change_note(token, csrf, record_id, key, expected, content, False)

    def confirm_note(self, token, csrf, record_id, *, key, expected):
        return self._change_note(token, csrf, record_id, key, expected, None, True)

    def _change_note(self, token, csrf, record_id, key, expected, content, confirm):
        record_id = _id(record_id)

        def operation(connection, owner):
            row = self._owned(connection, notes, owner, record_id)
            self._expected(connection, owner, notes, row, expected)
            if row["state"] != "draft":
                raise VersionConflict(self._note(connection, owner, record_id))
            value = content
            if value is None:
                value = NoteContent.model_validate(
                    self._note(connection, owner, record_id)["content"]
                )
            if confirm:
                validate_confirmation(value, row["trade_date"])
            state, version = ("confirmed" if confirm else "draft"), expected + 1
            self._revision(connection, owner, record_id, version, state, value)
            connection.execute(
                update(notes)
                .where(
                    notes.c.id == record_id,
                    notes.c.owner_id == owner,
                    notes.c.version == expected,
                )
                .values(version=version, state=state, updated_at=self._now())
            )
            return {"kind": "note", "id": record_id, "version": version}

        return self._command(
            token,
            csrf,
            key,
            "note.confirm" if confirm else "note.save",
            locals_payload(
                id=record_id,
                expected=expected,
                content=content.model_dump(mode="json") if content else None,
            ),
            operation,
        )

    def fork_note(
        self, token, csrf, record_id, *, version, key, content=None, snapshot_id=None
    ):
        record_id = _id(record_id)
        _version(version)
        if snapshot_id is not None:
            snapshot_id = _id(snapshot_id)
        if content is not None:
            content = NoteContent.model_validate(content.model_dump())

        def operation(connection, owner):
            original = self._note(connection, owner, record_id, version)
            value = content or NoteContent.model_validate(original["content"])
            return self._new_note(
                connection,
                owner,
                snapshot_id if snapshot_id is not None else original["snapshot_id"],
                original["security_id"],
                value,
                original["kind"],
                original["template_version"],
                _id(original["revision_id"]),
                date.fromisoformat(original["trade_date"])
                if snapshot_id is None
                else None,
            )

        return self._command(
            token,
            csrf,
            key,
            "note.fork",
            locals_payload(
                id=record_id,
                version=version,
                content=content.model_dump(mode="json") if content else None,
                snapshot_id=snapshot_id,
            ),
            operation,
        )

    def create_template(self, token, csrf, *, key, snapshot_id):
        snapshot_id = _id(snapshot_id)

        def operation(connection, owner):
            snapshot = self._snapshot(connection, snapshot_id)
            return self._new_note(
                connection,
                owner,
                snapshot.id,
                None,
                template_content(snapshot.trade_date),
                "daily",
                TEMPLATE_VERSION,
            )

        return self._command(
            token, csrf, key, "template.create", {"snapshot_id": snapshot_id}, operation
        )

    def get_note(self, token, record_id, version=None):
        with self.database.engine.begin() as connection:
            identity = self.owners.authorize_connection(connection, token)
            return self._note(connection, identity.owner_id, record_id, version)

    def _list(self, token, table, page, page_size, *, record_id=None, state=None):
        _page(page, page_size)
        with self.database.engine.begin() as connection:
            owner = self.owners.authorize_connection(connection, token).owner_id
            query = select(table).where(table.c.owner_id == owner)
            if record_id is not None:
                self._owned(connection, notes, owner, record_id)
                query = query.where(table.c.note_id == _id(record_id))
            if state is not None:
                if state not in ("draft", "confirmed"):
                    raise ValueError("Invalid state")
                query = query.where(notes.c.state == state)
            time_field = (
                "updated_at"
                if table in (notes, watch)
                else "saved_at"
                if table is revisions
                else "created_at"
            )
            rows = (
                connection.execute(
                    query.order_by(table.c[time_field].desc(), table.c.id)
                    .limit(page_size + 1)
                    .offset((page - 1) * page_size)
                )
                .mappings()
                .all()
            )
            if table in (notes, watch):
                self._lock_sources(
                    connection, [row["snapshot_id"] for row in rows[:page_size]]
                )
            facts = TransactionFacts(connection, self.market)
            current = {}
            if table is notes and rows:
                current = {
                    row["note_id"]: row
                    for row in connection.execute(
                        select(revisions)
                        .join(
                            notes,
                            (notes.c.id == revisions.c.note_id)
                            & (notes.c.owner_id == revisions.c.owner_id)
                            & (notes.c.version == revisions.c.version),
                        )
                        .where(
                            revisions.c.owner_id == owner,
                            revisions.c.note_id.in_(
                                [row["id"] for row in rows[:page_size]]
                            ),
                        )
                    ).mappings()
                }
            items = []
            for row in rows[:page_size]:
                if table is notes:
                    revision = current.get(row["id"])
                    if revision is None:
                        raise NotFound
                    items.append(
                        self._note_result(
                            row,
                            revision,
                            facts.facts(row["snapshot_id"], row["security_id"]),
                        )
                    )
                elif table is watch:
                    items.append(
                        _json(
                            {
                                **dict(row),
                                "facts": facts.facts(
                                    row["snapshot_id"], row["security_id"]
                                ),
                            }
                        )
                    )
                else:
                    items.append(_json(dict(row)))
            return {
                "items": items,
                "page": page,
                "page_size": page_size,
                "has_more": len(rows) > page_size,
            }

    def list_notes(self, token, *, page=1, page_size=50, state=None):
        return self._list(token, notes, page, page_size, state=state)

    def list_watch(self, token, *, page=1, page_size=50):
        return self._list(token, watch, page, page_size)

    def list_revisions(self, token, record_id, *, page=1, page_size=50):
        return self._list(token, revisions, page, page_size, record_id=_id(record_id))

    def get_revision(self, token, record_id, version):
        return self.get_note(token, record_id, _version(version))

    def list_verifications(self, token, record_id, *, page=1, page_size=50):
        return self._list(
            token, verifications, page, page_size, record_id=_id(record_id)
        )

    def add_verification(
        self,
        token,
        csrf,
        record_id,
        *,
        key,
        version,
        outcome,
        evidence="",
        snapshot_id=None,
        supersedes=None,
    ):
        record_id = _id(record_id)
        _version(version)
        if snapshot_id is not None:
            snapshot_id = _id(snapshot_id)
        if supersedes is not None:
            supersedes = _id(supersedes)
        if outcome not in ("supported", "weakened", "refuted", "not_due"):
            raise ValueError("Invalid outcome")
        NoteContent(title="Verification", evidence=evidence)

        def operation(connection, owner):
            note = self._note(connection, owner, record_id, version)
            if note["state"] != "confirmed":
                raise ValueError("Only confirmed revisions can be verified")
            content = NoteContent.model_validate(note["content"])
            today = self._now().astimezone(ZoneInfo("Asia/Shanghai")).date()
            not_due = content.verification_date > today
            if not_due != (outcome == "not_due"):
                raise ValueError("Verification outcome does not match due date")
            if outcome != "not_due":
                if not evidence.strip() or snapshot_id is None:
                    raise ValueError("Verification requires evidence and snapshot")
                target = self._snapshot(connection, snapshot_id, note["security_id"])
                if target.trade_date < content.verification_date:
                    raise ValueError("Verification snapshot predates verification date")
            elif snapshot_id is not None:
                raise ValueError("Not-due record does not accept a fact snapshot")
            if supersedes is not None:
                prior = self._owned(connection, verifications, owner, supersedes)
                if (
                    prior["note_id"] != record_id
                    or str(prior["revision_id"]) != note["revision_id"]
                ):
                    raise NotFound
            new_id = uuid4()
            connection.execute(
                insert(verifications).values(
                    id=new_id,
                    owner_id=owner,
                    note_id=record_id,
                    revision_id=_id(note["revision_id"]),
                    outcome=outcome,
                    evidence=evidence,
                    snapshot_id=_id(snapshot_id) if snapshot_id else None,
                    supersedes=_id(supersedes) if supersedes else None,
                    created_at=self._now(),
                )
            )
            return {"kind": "verification", "id": new_id, "version": version}

        return self._command(
            token,
            csrf,
            key,
            "verification.add",
            locals_payload(
                id=record_id,
                version=version,
                outcome=outcome,
                evidence=evidence,
                snapshot_id=snapshot_id,
                supersedes=supersedes,
            ),
            operation,
        )


def locals_payload(**values):
    return values
