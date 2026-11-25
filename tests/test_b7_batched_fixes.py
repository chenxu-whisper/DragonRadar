"""B7 review regressions using only pure artifacts and transaction doubles."""

import copy
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from inspect import signature
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from test_b7_materialization import materialized
from test_b7_result_contracts import actual_result
from test_reset_backtest import _run_wf, _wf_config
from test_reset_backtest import run_repo as run_repo

from dragon_radar.research.cache_contract import SCHEMAS, compute_contract
from dragon_radar.research.repository import ResearchRunRepository
from dragon_radar.research.result_contracts import (
    ResearchDataUnavailable,
    canonical,
    validate_result,
)
from dragon_radar.research.worker import (
    ResearchBacktestWorker,
    finish_validated_result,
)


def folded_materialized():
    """Mirror actual WF artifacts with valid v2 context, not production inputs."""
    cfg = _wf_config()
    payload = _run_wf().model_dump(mode="json")
    _, template = materialized()
    payload["schema_version"] = SCHEMAS["v2"]
    payload["health"] = {
        "status": "unavailable",
        "reason": "factor_health_inputs_unavailable",
        "report": None,
    }
    line = copy.deepcopy(template["lineage"])
    days = [p["trade_date"] for p in payload["equity"]]
    domains = {d.domain: str(d.snapshot_id) for d in cfg.input_manifests.domains}
    line.update(
        anchor_sequence=900,
        requested_period=cfg.period.model_dump(mode="json"),
        actual_period=cfg.period.model_dump(mode="json"),
        sessions=days,
        frames=[
            {
                "trade_date": day,
                "publication_id": str(cfg.snapshot_id)
                if i == len(days) - 1
                else str(uuid4()),
                "publication_sequence": 900 if i == len(days) - 1 else (i + 1) * 100,
                "market_snapshot_id": domains["market"],
                "topic_snapshot_id": domains.get("topic"),
                "role_snapshot_id": domains.get("role"),
                "frame_sha256": sha256(day.encode()).hexdigest(),
                "visible_security_ids": ["X1", "X2"],
                "as_of": day + "T15:00:00Z",
            }
            for i, day in enumerate(days)
        ],
    )
    payload["lineage"] = line
    return cfg, payload


@pytest.mark.parametrize("section", ["root", "ablation", "walk_forward"])
def test_f5_numeric_research_only_is_not_boolean(section):
    cfg, payload = (
        actual_result()
        if section != "walk_forward"
        else (_wf_config(), _run_wf().model_dump(mode="json"))
    )
    target = payload if section == "root" else payload[section]
    target["research_only"] = 1
    with pytest.raises(ValueError):
        validate_result(payload, cfg)


@pytest.mark.parametrize("kind", ["trades", "rejections"])
def test_f3_fold_artifacts_must_be_signal_day_visible(kind):
    cfg, payload = folded_materialized()
    validate_result(payload, cfg)
    fold = payload["walk_forward"]["folds"][0]
    if kind == "trades":
        fold[kind][0]["security_id"] = "UNARCHIVED"
    else:
        trade = fold["trades"][0]
        fold[kind].append(
            {
                **{
                    key: trade[key]
                    for key in ("side", "quantity", "signal_date", "trade_date")
                },
                "security_id": "UNARCHIVED",
                "reason": "suspended",
            }
        )
    payload["folds"] = copy.deepcopy(payload["walk_forward"]["folds"])
    with pytest.raises(ValueError):
        validate_result(payload, cfg)


def history_double():
    cfg, result = materialized()
    trusted = copy.deepcopy(result["lineage"]["frames"])
    rows = [
        {
            "id": UUID(f["publication_id"]),
            "publication_sequence": f["publication_sequence"],
            "trade_date": datetime.fromisoformat(f["trade_date"]).date(),
            "market_snapshot_id": UUID(f["market_snapshot_id"]),
            "topic_snapshot_id": None,
            "role_input_id": None,
            "published_at": datetime(2026, 1, 1, tzinfo=UTC),
            "status": "published",
        }
        for f in trusted
    ]
    cache = {
        "result": copy.deepcopy(result),
        "input_manifest": {"config": cfg.model_dump(mode="json"), "history": trusted},
    }

    class Connection:
        def execute(self, statement):
            if "research_backtest_cache_v2" in str(statement):
                return SimpleNamespace(
                    mappings=lambda: SimpleNamespace(first=lambda: cache)
                )
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))

    repo = ResearchRunRepository(
        None,
        None,
        SimpleNamespace(_authorized_records=lambda c, r: [object() for _ in r]),
    )
    return repo, Connection(), cfg, result, cache


def validate_history(repo, connection, cfg, result):
    kwargs = (
        {"config": cfg, "contract": compute_contract("v2")}
        if "config" in signature(repo._validate_history).parameters
        else {}
    )
    repo._validate_history(connection, result, **kwargs)


@pytest.mark.parametrize(
    "field,value",
    [
        ("frame_sha256", "f" * 64),
        ("as_of", "2025-01-06T00:00:00Z"),
    ],
)
def test_f2_history_uses_immutable_cache_mapping_not_result_facts(field, value):
    repo, connection, cfg, result, cache = history_double()
    result["lineage"]["frames"][0][field] = value
    cache["result"] = copy.deepcopy(result)
    validate_result(result, cfg)
    with pytest.raises(ResearchDataUnavailable):
        validate_history(repo, connection, cfg, result)


def test_f2_missing_trusted_history_is_unavailable_not_legacy_promotion():
    repo, connection, cfg, result, cache = history_double()
    del cache["input_manifest"]["history"]
    with pytest.raises(ResearchDataUnavailable):
        validate_history(repo, connection, cfg, result)


def test_f2_valid_history_reads_no_compute_and_v1_needs_no_mapping():
    repo, connection, cfg, result, cache = history_double()
    validate_history(repo, connection, cfg, result)
    _, legacy = actual_result()
    repo._validate_history(None, legacy)


class WorkerRepository:
    def __init__(self, cfg, contract):
        self.lease = SimpleNamespace(
            run=SimpleNamespace(
                config=cfg,
                result_schema_version=SCHEMAS["v2"],
                compute_contract_sha256=contract,
                max_wall_clock_ms=300000,
            )
        )
        self.loaded = False
        self.succeeded = False

    def claim(self, *args, **kwargs):
        return self.lease

    def load_input(self, lease):
        self.loaded = True
        return object()

    def fail(self, lease, failure):
        return failure

    def succeed(self, *args, **kwargs):
        self.succeeded = True
        return {"code": "succeeded"}


def test_f1_worker_rejects_different_server_contract_before_compute():
    cfg, result = materialized()
    repo = WorkerRepository(
        cfg, compute_contract("v2", approved_descriptor={"method": "old"})
    )

    class Compute:
        compute_contract_sha256 = compute_contract("v2")

        def __call__(self, *args):
            pytest.fail("mismatched worker must not compute")

    outcome = ResearchBacktestWorker(
        repo, worker_id="test", compute=Compute()
    ).run_once()
    assert outcome == {"code": "compute_contract_mismatch"}
    assert not repo.loaded and not repo.succeeded


@pytest.mark.parametrize("executing", [None, compute_contract("v2")])
def test_f1_finish_rejects_missing_or_different_executing_contract(executing):
    cfg, result = materialized()
    repo = WorkerRepository(
        cfg, compute_contract("v2", approved_descriptor={"method": "old"})
    )
    kwargs = (
        {"executing_contract": executing}
        if "executing_contract" in signature(finish_validated_result).parameters
        else {}
    )
    assert finish_validated_result(repo, repo.lease, result, **kwargs) == {
        "code": "compute_contract_mismatch",
    }
    assert not repo.succeeded


def test_db_f1_nonzero_v1_rejects_unannotated_compute_and_direct_finish(run_repo):
    from sqlalchemy import text
    from test_reset_backtest import _ex15_result, _submission

    ctx = run_repo
    submission = _submission(ctx.publication.id)
    queued = ctx.runs.submit(ctx.session.token, ctx.session.csrf, submission)
    payload = _ex15_result(submission)
    lease = ctx.runs.claim("direct")
    for identity in (None, "f" * 64):
        with pytest.raises(ValueError, match="executing compute"):
            ctx.runs.succeed(lease, payload, executing_contract=identity)
    with ctx.runs.database.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT count(*) FROM research_backtest_cache")) == 0
        )
    ctx.runs.fail(lease, {"code": "test_finished"})
    queued = ctx.runs.submit(
        ctx.session.token,
        ctx.session.csrf,
        submission.model_copy(update={"idempotency_key": uuid4()}),
    )

    def unannotated(*args):
        pytest.fail("unannotated new-v1 compute must not execute")

    record = ResearchBacktestWorker(
        ctx.runs, worker_id="unannotated", compute=unannotated
    ).run_once()
    assert record.id == queued.id
    assert record.failure["code"] == "compute_contract_mismatch"


def test_db_f1_zero_v1_legacy_is_distinguished_and_can_finish(run_repo):
    import json

    from test_reset_backtest import _ex15_result, _insert_run, _submission

    ctx = run_repo
    submission = _submission(ctx.publication.id)
    cfg = submission.config
    with ctx.runs.database.engine.begin() as connection:
        owner = ctx.owners.authorize_connection(connection, ctx.session.token).owner_id
        _insert_run(
            connection,
            owner,
            cfg.snapshot_id,
            request_hash=cfg.config_hash,
            config_hash=cfg.config_hash,
            input_manifest=json.dumps({"config": cfg.model_dump(mode="json")}),
        )

    def unannotated(snapshot, config, checkpoint):
        checkpoint()
        return _ex15_result(submission)

    record = ResearchBacktestWorker(
        ctx.runs, worker_id="legacy", compute=unannotated
    ).run_once()
    assert record.status == "succeeded"
    assert record.compute_contract_sha256 == "0" * 64


def test_db_f2_v2_mapping_persists_atomically_and_tampered_get_fails(run_repo):
    from factories import DAYS, NOW, batch
    from sqlalchemy import select, update

    from dragon_radar.publication.models import PublicationCommand
    from dragon_radar.publication.service import PublicationService
    from dragon_radar.publication.tables import publications
    from dragon_radar.research.engine import FactorFrame, FactorPoint, run_backtest
    from dragon_radar.research.materialization import (
        authorized_frame_mapping,
        materialize_result,
    )
    from dragon_radar.research.models import BacktestConfig, BacktestSubmission
    from dragon_radar.research.pit import PitAnchor, PitFrame
    from dragon_radar.research.tables import (
        research_backtest_cache_v2,
        research_backtest_runs,
    )

    ctx = run_repo
    market_repo = ctx.runs.publications.market
    database = ctx.runs.database
    frames, pubs = [], []
    for index, day in enumerate(DAYS):
        as_of = datetime(day.year, day.month, day.day, 17, tzinfo=UTC)
        raw = batch(
            trade_date=day,
            as_of=as_of,
            bars=tuple(bar for bar in batch().bars if bar.trade_date <= day),
        )
        market = market_repo.publish(
            market_repo.import_batch(raw, key=f"b7-market-{index}").id
        )
        pub = PublicationService(database, market_repo, clock=lambda: NOW).publish(
            PublicationCommand(
                trade_date=day,
                market_snapshot_id=market.id,
                disabled_domains=("topic", "news", "regulation", "role"),
            ),
            key=f"b7-pub-{index}",
        )
        with database.engine.connect() as connection:
            sequence = connection.scalar(
                select(publications.c.publication_sequence).where(
                    publications.c.id == pub.id
                )
            )
        frame = PitFrame(trade_date=day, publication_sequence=sequence, market=raw)
        frames.append(frame)
        pubs.append(
            {
                "snapshot_id": str(pub.id),
                "publication_sequence": sequence,
                "market_snapshot_id": str(market.id),
                "topic_snapshot_id": None,
                "published_at": pub.published_at.isoformat(),
                "frame_sha256": sha256(
                    canonical(frame.model_dump(mode="json"))
                ).hexdigest(),
            }
        )
    repo = ResearchRunRepository(
        database,
        ctx.owners,
        ctx.runs.publications,
        clock=lambda: NOW,
        result_version="v2",
    )
    context = repo.input_manifest(ctx.session.token, pub.id)
    cfg = BacktestConfig.model_validate_json(
        canonical(
            {
                "snapshot_id": str(pub.id),
                "strategy_id": "factor-cross-sectional-topn-v1",
                "parameters": {
                    "factor_id": "FAC-R01",
                    "top_n": 1,
                    "rebalance_period": 1,
                },
                "period": {"start": str(DAYS[0]), "end": str(DAYS[-1])},
                "cost_model": {
                    "commission_rate": "0.0003",
                    "stamp_tax_rate": "0.001",
                    "slippage_rate": "0.0005",
                    "min_commission": "5",
                },
                "execution_model": {},
                "experiment": {"random_seed": 1, "legs": ["baseline"]},
                "input_manifests": {
                    k: v for k, v in context.items() if k != "snapshot_id"
                },
            }
        )
    )
    anchor = PitAnchor(
        publication_sequence=sequence, trade_date=DAYS[-1], status="published"
    )
    factors = tuple(
        FactorFrame(
            trade_date=day,
            factor_id="FAC-R01",
            metric="relative_strength_20",
            window="20d",
            unit="ratio",
            points=(FactorPoint(security_id="A", value=Decimal("1")),),
        )
        for day in DAYS
    )
    payload = materialize_result(
        run_backtest(cfg, frames, factors, anchor=anchor).model_dump(mode="json"),
        cfg,
        frames,
        {"FAC-R01": factors},
        anchor=anchor,
        publications=pubs,
        computed_at=NOW,
    )
    history = authorized_frame_mapping(frames, anchor=anchor, publications=pubs)
    submitted = repo.submit(
        ctx.session.token,
        ctx.session.csrf,
        BacktestSubmission(config=cfg, idempotency_key=uuid4()),
    )
    lease = repo.claim("b7")
    corrupt = copy.deepcopy(payload)
    corrupt["lineage"]["frames"][0]["frame_sha256"] = "f" * 64
    with pytest.raises(ResearchDataUnavailable):
        repo.succeed(
            lease,
            corrupt,
            executing_contract=compute_contract("v2"),
            trusted_history=history,
        )
    with database.engine.connect() as connection:
        assert connection.scalar(select(research_backtest_cache_v2.c.id)) is None
    complete = repo.succeed(
        lease,
        payload,
        executing_contract=compute_contract("v2"),
        trusted_history=history,
    )
    with database.engine.connect() as connection:
        manifest = connection.scalar(
            select(research_backtest_cache_v2.c.input_manifest)
        )
    assert manifest["history"] == history
    assert repo.get(ctx.session.token, submitted.id).result == complete.result
    reused = repo.submit(
        ctx.session.token,
        ctx.session.csrf,
        BacktestSubmission(config=cfg, idempotency_key=uuid4()),
    )
    assert reused.status == "succeeded" and reused.attempt_count == 0
    for field, value in (
        ("frame_sha256", "f" * 64),
        ("as_of", str(DAYS[0]) + "T00:00:00Z"),
    ):
        corrupt = copy.deepcopy(payload)
        corrupt["lineage"]["frames"][0][field] = value
        with database.engine.begin() as connection:
            connection.execute(
                update(research_backtest_runs)
                .where(research_backtest_runs.c.id == submitted.id)
                .values(result=corrupt)
            )
        with pytest.raises(ResearchDataUnavailable):
            repo.get(ctx.session.token, submitted.id)
