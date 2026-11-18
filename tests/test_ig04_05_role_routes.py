"""IG04/05 route projection tests. No database, network, or role recomputation."""

import json
from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import urlencode
from uuid import UUID

import pytest
from factories import DAYS, bar, batch, state
from starlette.requests import Request
from test_ig04_05_materialization import event

from dragon_radar.http.errors import DataUnavailable
from dragon_radar.http.routes import roles
from dragon_radar.publication.models import PublicationResult, PublishedSnapshot
from dragon_radar.publication.service import _stored_json
from dragon_radar.readmodels.market import materialize_market
from dragon_radar.storage.repository import Forbidden

ANCHOR = UUID("00000000-0000-4000-8000-000000000041")
HISTORY = UUID("00000000-0000-4000-8000-000000000042")
LATE_REVISION = UUID("00000000-0000-4000-8000-000000000043")


def publication(identifier=ANCHOR, day=DAYS[-1], *, name="Anchor A", sector="topic-a"):
    market = batch(
        trade_date=day,
        expected_security_ids=("A",),
        security_states=(state("A", name=name),),
        bars=tuple(bar("A", trade_date=session) for session in DAYS if session <= day),
        limit_up_events=(event("A", day, 2),),
    )
    payload = materialize_market(market, None)
    payload["overview"]["hot_sectors"] = [
        {"sector_id": sector, "name": "Archived Topic A"}
    ]
    role_payload = {
        "role_run_id": str(identifier),
        "trade_date": day.isoformat(),
        "rule_version": "role-v1",
        "benchmark_id": "CSI300",
        "topics": [
            {
                "topic_id": sector,
                "member_ids": ["A"],
                "statuses": [
                    {
                        "role": "trend",
                        "capability": "ready",
                        "status": "ready",
                        "expected": 1,
                        "valid": 1,
                        "legal_excluded": 0,
                        "missing": 0,
                        "reasons": [],
                    }
                ],
                "results": [
                    {
                        "security_id": "A",
                        "role": "trend",
                        "internal_status": "candidate",
                        "tie_rank": 1,
                        "factors": [],
                        "reasons": [],
                    }
                ],
                "ladder": [
                    {
                        "security_id": "A",
                        "trade_date": day.isoformat(),
                        "closing_limit_streak": 2,
                        "broken_board": False,
                        "touched_not_sealed": False,
                        "reasons": [],
                    }
                ],
            }
        ],
    }
    results = tuple(
        PublicationResult(
            publication_id=identifier,
            domain=domain,
            input_id=None,
            status="ready",
            payload=_stored_json(value),
            coverage={"expected": 1, "valid": 1, "legal_excluded": 0, "missing": 0},
            quality_flags=(),
        )
        for domain, value in (("market", payload), ("role", role_payload))
    )
    return PublishedSnapshot(
        SimpleNamespace(id=identifier, trade_date=day, status="published"),
        SimpleNamespace(
            batch=market,
            manifest={"rule_version": "market-v1"},
            quality_report={
                "coverage": {
                    "expected": 1,
                    "valid": 1,
                    "legal_excluded": 0,
                    "missing": 0,
                },
                "issues": [],
            },
        ),
        results,
    )


def replace_payload(snapshot, domain, payload):
    snapshot.results = tuple(
        result.model_copy(update={"payload": payload})
        if result.domain == domain
        else result
        for result in snapshot.results
    )


class Repository:
    def __init__(self, anchor, history=()):
        self.anchor = anchor
        self.history = list(history)
        self.window_calls = []
        self.denied = False

    def get_snapshot(self, identifier):
        if self.denied:
            raise Forbidden("synthetic revocation")
        assert identifier == self.anchor.id, "must select the explicit anchor"
        return self.anchor

    def list_published_window(self, identifier, start, end):
        if self.denied:
            raise Forbidden("synthetic revocation")
        self.window_calls.append((identifier, start, end))
        return list(self.history)


def request(repository, **query):
    values = {
        "date": repository.anchor.trade_date.isoformat(),
        "snapshot_id": str(repository.anchor.id),
        **query,
    }
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/limit-up/ladder",
            "query_string": urlencode(values).encode(),
            "app": SimpleNamespace(state=SimpleNamespace(repository=repository)),
        }
    )


def decode(response):
    return json.loads(response.body)


def detail_archive():
    member = {
        "security_id": "A",
        "name": "Archived A",
        "status": "valid",
        "return": 0.0,
        "direction": "flat",
        "sealed": True,
        "reasons": [],
    }
    coverage = {"expected": 1, "valid": 1, "excluded": 0, "missing": 0}
    return {
        "trade_date": DAYS[-1].isoformat(),
        "previous_trade_date": DAYS[-2].isoformat(),
        "promotion": {"coverage": coverage, "members": [deepcopy(member)]},
        "feedback": {
            "coverage": coverage,
            "members": [deepcopy(member)],
            "direction_counts": {"up": 0, "down": 0, "flat": 1},
        },
    }


def with_detail(snapshot, block=None, version=1):
    payload = snapshot.result("market").model_dump(mode="json")["payload"]
    sentiment = payload["overview"]["sentiment"]
    sentiment["promote_rate"] = {
        "yesterday_ge2": 1,
        "numerator": 1,
        "denominator": 1,
        "value": 1.0,
        "excluded": 0,
        "missing": 0,
        "reason": None,
    }
    sentiment["yesterday_limit_up_median_return"] = 0.0
    sentiment["yesterday_limit_up_return_coverage"] = {
        "expected": 1,
        "valid": 1,
        "excluded": 0,
        "missing": 0,
    }
    sentiment["yesterday_limit_up_return_reason"] = None
    sentiment["continuity_detail_version"] = version
    sentiment["continuity_detail"] = block if block is not None else detail_archive()
    replace_payload(snapshot, "market", payload)
    return snapshot


def test_ladder_projects_actual_publications_and_appends_exact_anchor_once():
    anchor = publication()
    historical = publication(HISTORY, DAYS[-2], name="Historical A")
    repository = Repository(anchor, [historical])
    result = decode(roles.ladder(request(repository, days=3)))
    assert repository.window_calls == [(ANCHOR, DAYS[-3], DAYS[-1])]
    history = result["data"]["history"]
    assert history["days"] == 3
    assert history["window"] == {"start": str(DAYS[-3]), "end": str(DAYS[-1])}
    assert [row["trade_date"] for row in history["rows"]] == [
        str(day) for day in DAYS[-3:]
    ]
    available = [row for row in history["rows"] if row["status"] == "available"]
    assert [row["snapshot_id"] for row in available] == [str(HISTORY), str(ANCHOR)]
    assert [row["cells"][0]["stocks"][0]["name"] for row in available] == [
        "Historical A",
        "Anchor A",
    ]
    assert all(row["rule_version"] == "role-v1" for row in available)
    assert LATE_REVISION not in {row["snapshot_id"] for row in history["rows"]}
    missing = history["rows"][0]
    assert missing["status"] == "unavailable"
    assert missing["snapshot_id"] is None and missing["cells"] is None
    assert missing["reasons"] == ["publication_unavailable"]


def test_ladder_sector_history_is_filtered_even_when_anchor_heatmap_has_counts():
    repository = Repository(
        publication(),
        [publication(HISTORY, DAYS[-2], sector="topic-other")],
    )
    result = decode(roles.ladder(request(repository, days=2, sector="topic-a")))
    history = result["data"]["history"]
    assert history["sector"] == "topic-a"
    past, anchor = history["rows"]
    assert past["snapshot_id"] == str(HISTORY)
    assert past["total"] == 0 and past["cells"] == []
    assert anchor["total"] == 1
    assert anchor["cells"][0]["stocks"][0]["sector_id"] == "topic-a"


def test_ladder_cards_use_archived_return_seal_and_actual_topic_context():
    snapshot = publication()
    payload = snapshot.result("market").model_dump(mode="json")["payload"]
    payload["overview"]["returns"] = [
        {
            "security_id": "A",
            "value": 0.1234,
            "basis": "explicit_or_prior_session_close",
            "warnings": [],
        }
    ]
    replace_payload(snapshot, "market", payload)
    stock = decode(roles.ladder(request(Repository(snapshot))))["data"]["cells"][0][
        "stocks"
    ][0]
    assert stock.get("change_ratio") == 0.1234, "do not recompute from the batch close"
    assert stock.get("topics") == [{"sector_id": "topic-a", "name": "Archived Topic A"}]
    assert stock.get("role_evidence") == [
        {
            "role_type": "trend_leader",
            "sector_id": "topic-a",
            "evidence_status": "insufficient",
        }
    ]
    assert stock.get("seal") == {
        "is_limit_up": True,
        "touched_limit": True,
        "first_seal_time": None,
        "last_seal_time": None,
        "break_count": None,
        "seal_amount": None,
        "seal_amount_unit": None,
        "continuous_boards": 2,
        "reason": "intraday_seal_fields_unavailable",
    }


@pytest.mark.parametrize("days", ["0", "251", "-1", "1.5", "true"])
def test_ladder_invalid_days_never_reads_history(days):
    repository = Repository(publication())
    with pytest.raises(ValueError):
        roles.ladder(request(repository, days=days))
    assert repository.window_calls == []


def test_ladder_wrong_anchor_date_is_not_replaced_with_latest_publication():
    repository = Repository(publication())
    with pytest.raises(ValueError, match="date"):
        roles.ladder(request(repository, date="2026-09-07"))
    assert repository.window_calls == []


def test_ladder_revocation_is_not_masked_as_missing_history_or_cached_data():
    repository = Repository(publication())
    repository.denied = True
    with pytest.raises(Forbidden):
        roles.ladder(request(repository))
    assert repository.window_calls == []


def test_continuity_legacy_archive_keeps_summary_but_marks_detail_unavailable():
    snapshot = publication()
    payload = snapshot.result("market").model_dump(mode="json")["payload"]
    sentiment = payload["overview"]["sentiment"]
    sentiment.pop("continuity_detail", None)
    sentiment.pop("continuity_detail_version", None)
    replace_payload(snapshot, "market", payload)
    result = decode(roles.continuity(request(Repository(snapshot))))["data"]
    assert result["promote_rate"]["yesterday_ge2"] == 0
    assert result.get("details") == {
        "status": "unavailable",
        "version": None,
        "data": None,
        "reasons": ["continuity_detail_unavailable"],
    }


def test_continuity_reads_exact_archive_and_never_rebuilds_from_today():
    snapshot = with_detail(publication())
    result = decode(roles.continuity(request(Repository(snapshot))))["data"]
    assert result.get("details") == {
        "status": "available",
        "version": 1,
        "reasons": [],
        "data": detail_archive(),
    }
    assert (
        snapshot.result("market").payload["overview"]["sentiment"][
            "continuity_detail_version"
        ]
        == 1
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_date",
        "wrong_previous_date",
        "duplicate_member",
        "bad_coverage",
        "wrong_direction",
        "wrong_direction_count",
        "invented_return",
        "wrong_promotion_count",
        "wrong_median",
        "missing_block",
        "boolean_version",
    ],
)
def test_continuity_corrupt_v1_archive_fails_closed(mutation):
    block = detail_archive()
    if mutation == "wrong_date":
        block["trade_date"] = "2026-09-07"
    elif mutation == "wrong_previous_date":
        block["previous_trade_date"] = "2026-09-03"
    elif mutation == "duplicate_member":
        block["feedback"]["members"].append(deepcopy(block["feedback"]["members"][0]))
    elif mutation == "bad_coverage":
        block["feedback"]["coverage"]["expected"] = 2
    elif mutation == "wrong_direction":
        block["feedback"]["members"][0]["direction"] = "up"
    elif mutation == "wrong_direction_count":
        block["feedback"]["direction_counts"] = {"up": 1, "down": 0, "flat": 0}
    elif mutation == "invented_return":
        block["feedback"]["members"][0].update(status="missing", reasons=["gap"])
    elif mutation == "wrong_promotion_count":
        block["promotion"]["members"][0]["sealed"] = False
    elif mutation == "wrong_median":
        block["feedback"]["members"][0]["return"] = 0.3
        block["feedback"]["members"][0]["direction"] = "up"
        block["feedback"]["direction_counts"] = {"up": 1, "down": 0, "flat": 0}
    snapshot = with_detail(
        publication(), block, version=True if mutation == "boolean_version" else 1
    )
    if mutation == "missing_block":
        payload = snapshot.result("market").model_dump(mode="json")["payload"]
        payload["overview"]["sentiment"].pop("continuity_detail")
        replace_payload(snapshot, "market", payload)
    with pytest.raises(DataUnavailable):
        roles.continuity(request(Repository(snapshot)))


def test_continuity_boolean_coverage_is_corruption_not_an_integer_count():
    block = detail_archive()
    block["feedback"]["coverage"]["valid"] = True
    with pytest.raises(DataUnavailable):
        roles.continuity(request(Repository(with_detail(publication(), block))))


def test_ladder_history_without_role_domain_discloses_missing_not_current_data():
    anchor = publication()
    historical = publication(HISTORY, DAYS[-2])
    historical.results = tuple(
        result for result in historical.results if result.domain != "role"
    )
    result = decode(roles.ladder(request(Repository(anchor, [historical]), days=2)))
    row = result["data"]["history"]["rows"][0]
    assert row["status"] == "unavailable"
    assert row["snapshot_id"] == str(HISTORY)
    assert row["cells"] is None
    assert row["reasons"]


@pytest.mark.parametrize(
    "fact", ["return", "null_return", "sealed", "null_sealed", "reasons"]
)
def test_continuity_shared_member_facts_cannot_contradict_between_pools(fact):
    block = detail_archive()
    promotion = block["promotion"]["members"][0]
    feedback = block["feedback"]["members"][0]
    if fact == "return":
        promotion.update({"return": -0.1, "direction": "down"})
    elif fact == "null_return":
        promotion.update({"return": None, "direction": None})
    elif fact == "sealed":
        feedback["sealed"] = False
    elif fact == "null_sealed":
        feedback["sealed"] = None
    else:
        promotion["reasons"] = ["daily_return_unavailable"]
    with pytest.raises(DataUnavailable):
        roles.continuity(request(Repository(with_detail(publication(), block))))


def continuity_materialized_publication(suspended=(), missing=(), no_reference=False):
    from test_ig04_05_materialization import feedback_batch

    snapshot = publication()
    market = feedback_batch(suspended=suspended, missing=missing)
    if no_reference:
        market = market.model_copy(
            update={
                "bars": tuple(
                    row.model_copy(update={"previous_close": None})
                    if row.security_id == "A" and row.trade_date == DAYS[-1]
                    else row
                    for row in market.bars
                    if not (row.security_id == "A" and row.trade_date == DAYS[-2])
                )
            }
        )
    payload = materialize_market(market, None)
    replace_payload(snapshot, "market", _stored_json(payload))
    return snapshot


@pytest.mark.parametrize(
    "suspended,missing,no_reference",
    [((), (), False), (("B",), (), False), ((), ("B",), False), ((), (), True)],
)
def test_continuity_accepts_real_materialized_shared_facts(
    suspended, missing, no_reference
):
    snapshot = continuity_materialized_publication(suspended, missing, no_reference)
    result = decode(roles.continuity(request(Repository(snapshot))))
    assert result["data"]["details"]["status"] == "available"
    for pool in ("promotion", "feedback"):
        for member in result["data"]["details"]["data"][pool]["members"]:
            assert member["return"] is None or isinstance(
                member["return"], (int, float)
            )
    if no_reference:
        detail = result["data"]["details"]["data"]
        assert detail["promotion"]["members"][0]["status"] == "valid"
        assert detail["feedback"]["members"][0]["status"] == "missing"
        assert detail["feedback"]["members"][0]["reasons"] == [
            "daily_return_unavailable"
        ]


def role_change_publication():
    snapshot = publication(name="Current batch name must not be used")
    market = materialize_market(
        batch(
            expected_security_ids=("A", "B"),
            security_states=(state("A", name="Zulu"), state("B", name="Alpha")),
            bars=tuple(
                bar(security_id, trade_date=day)
                for day in DAYS
                for security_id in ("A", "B")
            ),
        ),
        None,
    )
    replace_payload(snapshot, "market", market)
    payload = snapshot.result("role").model_dump(mode="json")["payload"]
    payload["topics"][0]["role_changes"] = [
        {
            "id": f"change-{security_id}",
            "security_id": security_id,
            "role": "trend",
            "name": "Untrusted change name",
            "comparison_status": "comparable",
            "change_type": "exited",
            "membership_changed": False,
            "reasons": [],
        }
        for security_id in ("A", "B", "UNKNOWN")
    ]
    replace_payload(snapshot, "role", payload)
    return snapshot


def test_candidates_hydrate_changes_only_from_exact_publication_archive():
    snapshot = role_change_publication()
    original = snapshot.result("role").model_dump(mode="json")["payload"]
    result = decode(
        roles.candidates(request(Repository(snapshot), role="trend_leader"))
    )
    changes = result["data"]["role_changes"]
    assert [(row["security_id"], row.get("name")) for row in changes] == [
        ("A", "Zulu"),
        ("B", "Alpha"),
        ("UNKNOWN", None),
    ]
    assert "name" in changes[-1]
    assert snapshot.result("role").model_dump(mode="json")["payload"] == original
