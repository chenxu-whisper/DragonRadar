"""Exact source metadata identities and PIT, without latest fallback."""

from datetime import timedelta

import pytest
from test_reset_lhb import OPERATION_NOW, repo_batch
from test_reset_realtime import T0, _quote_batch

from dragon_radar.storage.repository import Forbidden, NotFound


@pytest.fixture
def realtime(database):
    from factories import grant

    from dragon_radar.realtime.repository import RealtimeRepository
    from dragon_radar.storage.repository import Repository

    def clock():
        return T0 + timedelta(hours=1)

    Repository(database, clock=clock).register_source(grant(source_id="licensed-feed"))
    return RealtimeRepository(database, clock=clock)


@pytest.fixture
def lhb(database):
    from factories import grant

    from dragon_radar.lhb.repository import LHBRepository
    from dragon_radar.storage.repository import Repository

    def clock():
        return OPERATION_NOW

    Repository(database, clock=clock).register_source(grant(source_id="lhb-source"))
    return LHBRepository(database, clock=clock)


def test_rt_requires_precise_kind_batch_and_point_in_time(realtime):
    assert hasattr(realtime, "get_lineage"), "Realtime lineage missing"
    batch = _quote_batch("b4-quote")
    realtime.ingest_quote_batch(batch, key="b4-quote")
    identity = dict(source_id="licensed-feed", kind="quotes", batch_id="b4-quote")
    with pytest.raises(NotFound):
        realtime.get_lineage(**identity, as_of=T0)
    result = realtime.get_lineage(**identity, as_of=T0 + timedelta(seconds=2))
    assert result["identity"] == {
        "source_id": "licensed-feed",
        "source_version": "v1",
        "kind": "quotes",
        "batch_id": "b4-quote",
    }
    assert result["clocks"]["source_event_at"]["state"] == "known"
    assert result["clocks"]["provider_published_at"]["state"] == "unknown"
    assert result["clocks"]["computed_at"]["state"] == "unknown"
    assert result["clocks"]["snapshot_published_at"]["state"] == "not_applicable"
    for changes in (
        {"batch_id": "unknown"},
        {"kind": "minutes"},
        {"source_id": "other"},
    ):
        with pytest.raises(NotFound):
            realtime.get_lineage(
                **{**identity, **changes}, as_of=T0 + timedelta(hours=1)
            )


def test_rt_revoked_current_grant_blocks_metadata(realtime):
    assert hasattr(realtime, "get_lineage"), "Realtime lineage missing"
    from factories import grant

    from dragon_radar.storage.repository import Repository

    realtime.ingest_quote_batch(_quote_batch("b4-revoke"), key="b4-revoke")
    Repository(realtime.database, clock=realtime.clock).register_source(
        grant(source_id="licensed-feed", local_display=False)
    )
    with pytest.raises(Forbidden):
        realtime.get_lineage(
            source_id="licensed-feed",
            kind="quotes",
            batch_id="b4-revoke",
            as_of=realtime.clock(),
        )


def test_lhb_exact_revision_unknown_and_late_do_not_select_current(lhb):
    assert hasattr(lhb, "get_lineage"), "LHB lineage missing"
    batch = repo_batch()
    lhb.ingest_batch(batch)
    revision = batch.disclosures[0]
    identity = dict(
        source_id=revision.source_id,
        disclosure_id=revision.disclosure_id,
        revision=revision.revision,
    )
    with pytest.raises(NotFound):
        lhb.get_lineage(
            **identity, as_of=revision.available_at - timedelta(microseconds=1)
        )
    result = lhb.get_lineage(**identity, as_of=revision.available_at)
    assert result["identity"] == {
        **identity,
        "source_version": revision.source_version,
    }
    assert result["clocks"]["first_received_at"]["state"] == "known"
    assert result["clocks"]["computed_at"]["state"] == "unknown"
    for changes in (
        {"revision": 999},
        {"disclosure_id": "unknown"},
        {"source_id": "other"},
    ):
        with pytest.raises(NotFound):
            lhb.get_lineage(**{**identity, **changes}, as_of=OPERATION_NOW)


def test_rt_minute_lineage_requires_parent_and_child_pit(realtime):
    from test_reset_realtime import _minute_batch, minute_bar

    bars = (minute_bar(minute=0), minute_bar(minute=1))
    batch = _minute_batch("b4-minutes", bars).model_copy(
        update={
            "available_at": T0 + timedelta(minutes=2),
        }
    )
    realtime.ingest_minute_batch(batch, key="b4-minutes")
    identity = dict(source_id="licensed-feed", kind="minutes", batch_id="b4-minutes")
    with pytest.raises(NotFound):
        realtime.get_lineage(**identity, as_of=bars[-1].available_at)
    response = realtime.get_lineage(**identity, as_of=batch.available_at)
    assert response["clocks"]["source_event_at"]["state"] == "mixed"
    assert response["clocks"]["source_event_at"]["last_at"] == bars[
        -1
    ].event_at.isoformat().replace("+00:00", "Z")
    anomaly = realtime.get_lineage(
        **{**identity, "kind": "anomalies"}, as_of=batch.available_at
    )
    assert anomaly["identity"]["kind"] == "anomalies"
    assert anomaly["clocks"]["computed_at"]["state"] == "unknown"


def test_lhb_new_revision_keeps_original_receipt_and_revocation_blocks_lineage(lhb):
    from factories import grant
    from test_reset_lhb import disclosure

    from dragon_radar.storage.repository import Repository

    original = disclosure()
    corrected = original.model_copy(
        update={
            "revision": 2,
            "received_at": original.received_at + timedelta(seconds=2),
            "available_at": original.available_at + timedelta(seconds=2),
        }
    )
    lhb.ingest_batch(repo_batch("b4-r1", disclosures=(original,)))
    lhb.ingest_batch(repo_batch("b4-r2", disclosures=(corrected,)))
    identity = dict(source_id=original.source_id, disclosure_id=original.disclosure_id)
    first = lhb.get_lineage(**identity, revision=1, as_of=OPERATION_NOW)
    second = lhb.get_lineage(**identity, revision=2, as_of=OPERATION_NOW)
    assert (
        first["clocks"]["first_received_at"]["at"]
        != second["clocks"]["first_received_at"]["at"]
    )
    assert first["identity"]["revision"] == 1
    with pytest.raises(NotFound):
        lhb.get_lineage(**identity, revision=2, as_of=original.available_at)
    Repository(lhb.database, clock=lhb.clock).register_source(
        grant(source_id=original.source_id, local_display=False)
    )
    with pytest.raises(Forbidden):
        lhb.get_lineage(**identity, revision=1, as_of=OPERATION_NOW)
