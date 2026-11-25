"""Q01 pure clocks: exact, immutable, and honest about partial observations."""

import importlib
import importlib.util
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

T0 = datetime(2026, 11, 24, 10, 30, tzinfo=UTC)


def clocks():
    name = "dragon_radar.domain.clocks"
    assert importlib.util.find_spec(name) is not None, "Q01 clock contract missing"
    return importlib.import_module(name)


def reference(**changes):
    return clocks().InputClockReference(
        **{
            "source_id": "supplier",
            "archive_type": "market_import_batch",
            "archive_id": str(uuid4()),
            "revision_key": "1",
            "source_version": "v1",
            "input_sha256": "a" * 64,
            "evidence_sha256": None,
            "source_event_at": None,
            "provider_published_at": None,
            "first_received_at": None,
            **changes,
        }
    )


@pytest.mark.parametrize(
    "values,state,at,first,last",
    [
        ((T0, T0), "known", T0, T0, T0),
        ((T0, T0 + timedelta(seconds=1)), "mixed", None, T0, T0 + timedelta(seconds=1)),
        ((T0, None), "unknown", None, T0, T0),
        ((), "unknown", None, None, None),
        ((None,), "unknown", None, None, None),
    ],
)
def test_aggregate_completeness(values, state, at, first, last):
    cell = clocks().aggregate_clock(values, reason="input_clock_unavailable")
    assert (cell.state, cell.at, cell.first_at, cell.last_at) == (
        state,
        at,
        first,
        last,
    )
    assert set(cell.model_dump()) == {"state", "at", "first_at", "last_at", "reason"}
    assert cell.reason == ("input_clock_unavailable" if state == "unknown" else None)


def test_aware_clock_normalizes_and_is_frozen():
    c = clocks()
    local = T0.astimezone(timezone(timedelta(hours=8)))
    cell = c.scalar_clock(local)
    assert cell.at.tzinfo == UTC
    with pytest.raises(ValidationError, match="frozen"):
        cell.at = T0
    with pytest.raises(ValueError, match="timezone"):
        c.scalar_clock(T0.replace(tzinfo=None))
    with pytest.raises(ValidationError):
        c.ClockCell(
            state="known", at=T0, first_at=T0, last_at=T0, reason="browser_clock"
        )


@pytest.mark.parametrize("value", [1795516200, 1795516200.5, True])
def test_epoch_numbers_are_not_attested_aware_timestamp_inputs(value):
    with pytest.raises(ValidationError):
        clocks().ClockCell(
            state="known", at=value, first_at=value, last_at=value, reason=None
        )


def test_evidence_requires_real_observation_and_hashes():
    c = clocks()
    data = reference().model_dump()
    data["evidence_sha256"] = "b" * 64
    with pytest.raises(ValidationError, match="observation"):
        c.InputClockEvidence(**data)
    valid = c.InputClockEvidence(**{**data, "first_received_at": T0})
    assert valid.first_received_at == T0
    with pytest.raises(ValidationError):
        reference(input_sha256="not-a-hash")
    with pytest.raises(ValidationError):
        reference(first_received_at=T0, evidence_sha256=None)


def test_domain_archive_rejects_duplicates_unsorted_budget_and_missing_compute():
    c = clocks()
    first = reference(source_id="a")
    second = reference(source_id="b")
    base = dict(
        domain="market", input_id=uuid4(), as_of=T0, status="ready", computed_at=T0
    )
    archive = c.DomainClockArchive(**base, inputs=(first, second))
    assert archive.schema_name == "dragon-radar.domain-clocks/v1"
    for inputs in ((first, first), (second, first), (first,) * 10001):
        with pytest.raises(ValidationError):
            c.DomainClockArchive(**base, inputs=inputs)
    with pytest.raises(ValidationError):
        c.DomainClockArchive(**{**base, "status": "missing"}, inputs=())


def test_six_names_and_page_clock_are_not_server_freshness():
    c = clocks()
    assert c.CLOCK_NAMES == (
        "source_event_at",
        "provider_published_at",
        "first_received_at",
        "computed_at",
        "snapshot_published_at",
        "page_updated_at",
    )
    block = c.domain_cells(
        c.DomainClockArchive(
            domain="market",
            input_id=uuid4(),
            as_of=T0,
            status="ready",
            inputs=(reference(),),
            computed_at=T0,
        ),
        published_at=T0 + timedelta(seconds=1),
    )
    assert tuple(block) == c.CLOCK_NAMES
    assert block["computed_at"].at == T0
    assert block["source_event_at"].state == "unknown"
    assert block["page_updated_at"].model_dump() == {
        "state": "not_applicable",
        "at": None,
        "first_at": None,
        "last_at": None,
        "reason": "browser_clock",
    }


def test_delay_is_exact_and_does_not_clamp_negative_or_partial_input():
    c = clocks()
    cells = {
        name: c.scalar_clock(T0 + timedelta(milliseconds=i * 1501))
        for i, name in enumerate(c.CLOCK_NAMES)
    }
    delays = c.clock_delays(cells)
    assert {value["milliseconds"] for value in delays.values()} == {1501}
    cells["first_received_at"] = c.aggregate_clock(
        (T0, None), reason="input_clock_unavailable"
    )
    assert c.clock_delays(cells)["completion_minus_receipt"]["milliseconds"] is None
    cells["provider_published_at"] = c.scalar_clock(T0 - timedelta(microseconds=1))
    assert c.clock_delays(cells)["provider_minus_event"]["reason"] == "negative_delay"
    with pytest.raises(ValueError, match="publication"):
        c.validate_publication_time((T0,), T0 - timedelta(microseconds=1))
