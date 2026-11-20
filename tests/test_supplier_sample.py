"""Engineering sample diagnostics; all data is synthetic and offline."""

from datetime import UTC, date, datetime

import pytest
from factories import bar, batch, state

SESSIONS = tuple(
    date.fromisoformat(value)
    for value in (
        "2026-09-03",
        "2026-09-04",
        "2026-09-07",
        "2026-09-08",
        "2026-09-09",
        "2026-09-10",
        "2026-09-11",
        "2026-09-14",
        "2026-09-15",
        "2026-09-16",
        "2026-09-17",
        "2026-09-18",
        "2026-09-21",
        "2026-09-22",
        "2026-09-23",
        "2026-09-24",
        "2026-09-25",
        "2026-09-28",
        "2026-09-29",
        "2026-09-30",
        "2026-10-01",
    )
)
CHECK_TIME = datetime(2026, 10, 2, 12, tzinfo=UTC)


def sample_payload():
    from dragon_radar.domain.models import ExchangeCalendar

    payload = batch(
        trade_date=SESSIONS[-1],
        as_of=CHECK_TIME,
        calendar=ExchangeCalendar(exchange="SSE", version="c1", sessions=SESSIONS),
        security_states=(
            state(
                code="600000",
                exchange="SSE",
                board="main",
                board_category="sh_main",
                available_at=datetime(2026, 9, 1, tzinfo=UTC),
            ),
        ),
        bars=tuple(
            bar(
                trade_date=day,
                open="10",
                high="11",
                low="9",
                limit_status="limited",
                limit_date=day,
                limit_up="11",
                limit_down="9",
            )
            for day in SESSIONS
        ),
    ).model_dump(mode="json")
    return {
        "schema": "dragon-radar-supplier-sample/v1",
        "kind": "test",
        "batch": payload,
        "benchmark_closes": {day.isoformat(): "3000" for day in SESSIONS},
        "decision_sessions": [SESSIONS[-1].isoformat()],
        "window": 20,
    }


def inspect_sample(payload):
    from dragon_radar.ingestion.sample_acceptance import SampleRequest, evaluate_sample

    return evaluate_sample(SampleRequest.model_validate(payload), now=CHECK_TIME)


def codes(report):
    return {item["code"] for item in report["issues"]}


def test_complete_sample_passes_engineering_only():
    report = inspect_sample(sample_payload())
    assert report["status"] == "pass"
    assert report["test_only"] is True
    assert report["production_approved"] is False
    assert report["historical_pit_status"] == "unverified"
    assert report["coverage"]["required_observations"] == 21
    assert report["coverage"]["valid_observations"] == 21


@pytest.mark.parametrize(
    "field", ["code", "listing_date", "is_st", "suspended", "is_delisting"]
)
def test_unknown_security_facts_block_sample(field):
    payload = sample_payload()
    payload["batch"]["security_states"][0][field] = None
    report = inspect_sample(payload)
    assert report["status"] == "blocked"
    assert "missing_security_fact" in codes(report)


def test_warmup_needs_twenty_one_sessions_and_matching_benchmark():
    payload = sample_payload()
    payload["batch"]["calendar"]["sessions"] = payload["batch"]["calendar"]["sessions"][
        1:
    ]
    report = inspect_sample(payload)
    assert "insufficient_warmup" in codes(report)
    assert report["status"] != "pass"
    payload = sample_payload()
    del payload["benchmark_closes"][SESSIONS[0].isoformat()]
    report = inspect_sample(payload)
    assert report["status"] == "blocked"
    assert "missing_benchmark" in codes(report)


def test_missing_historical_bar_blocks_factor_coverage():
    payload = sample_payload()
    payload["batch"]["bars"] = payload["batch"]["bars"][1:]
    report = inspect_sample(payload)
    assert report["status"] == "blocked"
    assert report["coverage"]["missing_observations"] == 1


@pytest.mark.parametrize("field", ["open", "high", "low", "volume", "amount"])
def test_close_only_or_missing_turnover_is_not_complete_candidate(field):
    payload = sample_payload()
    for row in payload["batch"]["bars"]:
        row[field] = None
    report = inspect_sample(payload)
    assert report["status"] != "pass"
    assert "missing_market_fact" in codes(report)


def test_unknown_limits_are_not_explicit_no_limit():
    payload = sample_payload()
    for row in payload["batch"]["bars"]:
        row.update(
            limit_status="unknown", limit_date=None, limit_up=None, limit_down=None
        )
    report = inspect_sample(payload)
    assert report["status"] == "blocked"
    assert "missing_limit_metadata" in codes(report)


@pytest.mark.parametrize(
    ("field", "value"),
    [("exchange", "SZSE"), ("board_category", "sz_main"), ("security_type", "index")],
)
def test_candidate_scope_mismatch_fails(field, value):
    payload = sample_payload()
    payload["batch"]["security_states"][0][field] = value
    assert inspect_sample(payload)["status"] == "fail"
    assert "scope_mismatch" in codes(inspect_sample(payload))


def test_duplicate_or_bad_units_fail_without_repair():
    payload = sample_payload()
    payload["batch"]["bars"].append(payload["batch"]["bars"][0])
    assert "duplicate_bar" in codes(inspect_sample(payload))
    payload = sample_payload()
    payload["batch"]["bars"][0]["volume_unit"] = "contract"
    assert inspect_sample(payload)["status"] == "fail"
    assert "unit_contract" in codes(inspect_sample(payload))


def test_expired_or_denied_storage_license_blocks_inspection_readiness():
    payload = sample_payload()
    payload["batch"]["grant"]["local_storage"] = False
    assert inspect_sample(payload)["status"] == "blocked"
    assert "storage_grant_denied" in codes(inspect_sample(payload))


def test_test_source_cannot_be_presented_as_supplier_sample():
    payload = sample_payload()
    payload["kind"] = "supplier_sample"
    assert inspect_sample(payload)["status"] == "fail"
    assert "test_source_mislabeled" in codes(inspect_sample(payload))


def test_currently_obtained_history_never_claims_historical_pit_approval():
    payload = sample_payload()
    payload["batch"]["provenance"] = "retrospective"
    report = inspect_sample(payload)
    assert report["historical_pit_status"] == "unverified"
    assert report["production_approved"] is False


@pytest.mark.parametrize("value", ["NaN", "0", "-1"])
def test_invalid_benchmark_fails(value):
    payload = sample_payload()
    payload["benchmark_closes"][SESSIONS[0].isoformat()] = value
    assert inspect_sample(payload)["status"] == "fail"


def test_reports_do_not_embed_security_values_or_license_text():
    import json

    payload = sample_payload()
    payload["batch"]["security_states"][0]["name"] = "SECRET_TOKEN_SAMPLE"
    payload["batch"]["grant"]["reference"] = "SECRET_CONTRACT_SAMPLE"
    serialized = json.dumps(inspect_sample(payload))
    assert "SECRET_" not in serialized
    assert "3000" not in serialized


def test_schema_rejects_float_benchmark_and_duplicate_decisions():
    from pydantic import ValidationError

    from dragon_radar.ingestion.sample_acceptance import SampleRequest

    payload = sample_payload()
    payload["benchmark_closes"][SESSIONS[0].isoformat()] = 3000.5
    with pytest.raises(ValidationError):
        SampleRequest.model_validate(payload)
    payload = sample_payload()
    payload["decision_sessions"] *= 2
    with pytest.raises(ValidationError):
        SampleRequest.model_validate(payload)


@pytest.mark.parametrize("target", ["benchmark", "decision", "window"])
def test_sample_wire_requires_iso_dates_and_integer_window(target):
    from pydantic import ValidationError

    from dragon_radar.ingestion.sample_acceptance import SampleRequest

    payload = sample_payload()
    if target == "benchmark":
        payload["benchmark_closes"] = {0: "3000"}
    elif target == "decision":
        payload["decision_sessions"] = [0]
    else:
        payload["window"] = 20.0
    with pytest.raises(ValidationError):
        SampleRequest.model_validate(payload)


def test_sample_identity_is_independent_of_object_key_order():
    payload = sample_payload()
    first = inspect_sample(payload)["input_sha256"]
    payload["benchmark_closes"] = dict(
        reversed(list(payload["benchmark_closes"].items()))
    )
    assert inspect_sample(payload)["input_sha256"] == first


@pytest.mark.parametrize("index", [0, -1])
@pytest.mark.parametrize(
    "field,value", [("high", "12"), ("low", "8"), ("open", "12"), ("close", "8")]
)
def test_all_ohlc_prices_must_match_explicit_session_limits(index, field, value):
    payload = sample_payload()
    payload["batch"]["bars"][index][field] = value
    report = inspect_sample(payload)
    assert report["status"] == "fail"
    assert "ohlc_outside_limits" in codes(report)
    assert report["coverage"]["valid_observations"] == 20
