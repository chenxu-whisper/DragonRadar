"""Explicit local production research inputs, not an API read dependency.

Canonical v1 wire contracts (all objects reject unknown fields):

* suppliers: schema, suppliers. Each supplier has supplier_id, source_id,
  source_version, domains (market/state/calendar/topic/benchmark), grant
  (SourceGrant), provenance="real", methods (factor id -> approved version).
* bundle: schema, candidate (exact code/dataset/rules/model/prompt/retrieval),
  methods, snapshot_id, publication_sequence, config (BacktestConfig), anchor
  (PitAnchor), frames (ordered PitFrames), publications, policy (UniversePolicy),
  receipts, benchmark_closes (ISO session -> exact Decimal string), factor_archive.
* publications: one per frame, with snapshot_id, market_snapshot_id,
  topic_snapshot_id (nullable), publication_sequence, published_at, frame_sha256.
* receipts: domain, source_id, source_version, record_sha256, source_ref (HTTPS),
  published_at, first_received_at, received_at, provenance="real". Hashes cover
  canonical JSON of individual DailyBars/SecurityStates/ExchangeCalendars,
  TopicDefinitions/Memberships/CoverageCells/PageManifest, or benchmark
  {"trade_date": ISO date, "close": exact Decimal string}. Receipts may predate
  a later frame; they must be original signed collection facts, never inferred.
* factor_archive: schema, method_version, frames (FactorFrames), lineage.
  Each lineage has trade_date, available_at, market_snapshot_id,
  topic_snapshot_id, publication_sequence, source_refs (receipt digests).
  Required frame dependencies must be referenced; future refs are forbidden.
* run: schema, candidate, snapshot_id, publication_sequence, config_sha256,
  input_sha256, result_sha256, started_at, finished_at, result (BacktestResult
  JSON). There is no "completed" flag. The three hashes cover canonical config,
  entire bundle, and actual deterministic result respectively.

Schemas are dragon-radar-production-{suppliers,input,factors,run}/v1.
Times are aware UTC ISO strings. Validity is half-open. Immutable archives use
<canonical-sha256>.json under an externally protected root, never overwrite.
Signatures and issuer approval are the caller's responsibility for the pure
validation functions. ProductionResearchCompute authenticates on EVERY call.
No raw assets, fixtures, new factor scores, or grants are synthesized here.
Walk-forward/AI/role domains require separately approved adapters and fail closed.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from dragon_radar.domain.models import SourceGrant
from dragon_radar.domain.quality import (
    bar_issues,
    grant_allows_local,
    validate_batch,
)
from dragon_radar.owner.files import secure_output
from dragon_radar.production.trust import (
    CANDIDATE_FIELDS,
    SCOPE_FIELDS,
    EvidenceError,
    TrustPolicy,
    canonical_json,
    read_asset,
    strict_json,
    verify_evidence,
)
from dragon_radar.publication.models import PublishedSnapshot
from dragon_radar.research.engine import FactorFrame, FactorPoint, run_backtest
from dragon_radar.research.models import BacktestConfig
from dragon_radar.research.pit import (
    PitAnchor,
    PitFrame,
    UniversePolicy,
    assemble_pit_window,
)
from dragon_radar.research.result_contracts import (
    RESEARCH_RESULT_SCHEMA_VERSION,
    validate_result,
)
from dragon_radar.research.worker import (
    ResearchInputsUnavailable,
    assert_compute_budget,
)
from dragon_radar.roles.factors import adjusted_price, period_return, relative_return
from dragon_radar.topics.quality import validate_topic_batch

INPUT_SCHEMA = "dragon-radar-production-input/v1"
RUN_SCHEMA = "dragon-radar-production-run/v1"
SUPPLIERS_SCHEMA = "dragon-radar-production-suppliers/v1"
FACTORS_SCHEMA = "dragon-radar-production-factors/v1"
BUNDLE_FIELDS = frozenset(
    {
        "schema",
        "candidate",
        "methods",
        "snapshot_id",
        "publication_sequence",
        "config",
        "anchor",
        "frames",
        "publications",
        "policy",
        "receipts",
        "benchmark_closes",
        "factor_archive",
    }
)
RUN_FIELDS = frozenset(
    {
        "schema",
        "candidate",
        "snapshot_id",
        "publication_sequence",
        "config_sha256",
        "input_sha256",
        "result_sha256",
        "started_at",
        "finished_at",
        "result",
    }
)
DOMAINS = frozenset({"market", "state", "calendar", "topic", "benchmark"})
METRICS = {f"relative_strength_{n}": n for n in (20, 60, 120)}
MAX_BYTES = 16 * 1024 * 1024


def _exact(value, fields):
    if not isinstance(value, Mapping) or set(value) != set(fields):
        raise ValueError("production input schema invalid")
    return value


def _text(value):
    if (
        type(value) is not str
        or not value
        or len(value) > 2048
        or value != value.strip()
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("production input text invalid")
    return value


def _real_source(value):
    value = _text(value)
    if re.search(r"(?i)(fixture|synthetic|test|demo|mock)", value):
        raise ValueError("fixture provenance is not production input")
    return value


def _hash(value):
    return sha256(canonical_json(value)).hexdigest()


def _digest(value):
    if type(value) is not str or not re.fullmatch("[a-f0-9]{64}", value):
        raise ValueError("production digest invalid")
    return value


def _now(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("production time must be aware")
    if value.utcoffset() is None:
        raise ValueError("production time must be aware")
    return value.astimezone(UTC)


def _time(value):
    if type(value) is not str or not re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(Z|\+00:00)", value
    ):
        raise ValueError("production timestamp invalid")
    return _now(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _model(model, value):
    return model.model_validate_json(canonical_json(value))


def _list(value, *, maximum=50000, empty=False):
    if type(value) is not list or len(value) > maximum or (not value and not empty):
        raise ValueError("production collection missing or unbounded")
    return value


def _versions(candidate):
    if not isinstance(candidate, Mapping) or not CANDIDATE_FIELDS <= set(candidate):
        raise ValueError("production candidate invalid")
    if (
        set(candidate)
        - CANDIDATE_FIELDS
        - {
            "methods",
            "regulation_rules",
            "rule_scope",
            "official_source_hosts",
        }
    ):
        raise ValueError("production candidate fields invalid")
    result = {key: _text(candidate[key]) for key in CANDIDATE_FIELDS}
    if not re.fullmatch(r"source-sha256:[a-f0-9]{64}", result["code"]):
        raise ValueError("production source identity invalid")
    return result


def _methods(value):
    if not isinstance(value, Mapping) or not value or len(value) > 128:
        raise ValueError("approved methods missing")
    for key, version in value.items():
        _text(key)
        _text(version)
    return dict(value)


def _suppliers(value, now, methods):
    _exact(value, {"schema", "suppliers"})
    if value["schema"] != SUPPLIERS_SCHEMA:
        raise ValueError("supplier schema invalid")
    result, identities = {}, set()
    for item in _list(value["suppliers"], maximum=128):
        _exact(
            item,
            {
                "supplier_id",
                "source_id",
                "source_version",
                "domains",
                "grant",
                "provenance",
                "methods",
            },
        )
        identity = _real_source(item["supplier_id"])
        source = _real_source(item["source_id"])
        version = _text(item["source_version"])
        if identity in identities or item["provenance"] != "real":
            raise ValueError("supplier provenance invalid")
        identities.add(identity)
        declared = _list(item["domains"], maximum=len(DOMAINS))
        if len(set(declared)) != len(declared) or not set(declared) <= DOMAINS:
            raise ValueError("supplier domains invalid")
        approved = _methods(item["methods"])
        if any(methods.get(k) != v for k, v in approved.items()):
            raise ValueError("supplier approved method mismatch")
        grant = _model(SourceGrant, item["grant"])
        if not grant_allows_local(grant, source_id=source, now=now):
            raise ValueError("supplier grant not live at use")
        for domain in declared:
            key = (domain, source, version)
            if key in result:
                raise ValueError("duplicate supplier source/domain")
            result[key] = (grant, approved)
    return result


def _receipts(value, suppliers, now):
    result = {}
    for item in _list(value):
        _exact(
            item,
            {
                "domain",
                "source_id",
                "source_version",
                "record_sha256",
                "source_ref",
                "published_at",
                "first_received_at",
                "received_at",
                "provenance",
            },
        )
        domain = _text(item["domain"])
        source = _real_source(item["source_id"])
        version = _text(item["source_version"])
        identity = _digest(item["record_sha256"])
        if item["provenance"] != "real":
            raise ValueError("receipt provenance invalid")
        reference = _real_source(item["source_ref"])
        url = urlsplit(reference)
        if url.scheme != "https" or not url.netloc or url.username or url.password:
            raise ValueError("receipt source reference invalid")
        published, first, received = (
            _time(item[k]) for k in ("published_at", "first_received_at", "received_at")
        )
        if not published <= first <= received <= now:
            raise ValueError("receipt time invalid; never backfill first reception")
        supplier = suppliers.get((domain, source, version))
        if supplier is None:
            raise ValueError("receipt source/domain not authorized")
        if not all(
            grant_allows_local(supplier[0], source_id=source, now=t)
            for t in (first, received)
        ):
            raise ValueError("supplier grant not live at receipt")
        key = (domain, identity)
        if key in result:
            raise ValueError("duplicate production receipt")
        result[key] = item
    return result


def _record_receipt(record, domain, batch, receipts, cutoff, used, dependency_times):
    identity = _hash(record.model_dump(mode="json"))
    item = receipts.get((domain, identity))
    if item is None:
        raise ValueError("raw record reception evidence missing")
    if (item["source_id"], item["source_version"]) != (
        batch.source_id,
        batch.source_version,
    ):
        raise ValueError("raw reception source binding mismatch")
    if not _time(item["received_at"]) <= cutoff:
        raise ValueError("raw record actually received after PIT cutoff")
    available = getattr(record, "available_at", None)
    if available is not None and (
        available > cutoff or _time(item["received_at"]) > available
    ):
        raise ValueError("raw availability precedes actual reception")
    record_times = [
        value
        for field in ("available_at", "complete_as_of")
        if (value := getattr(record, field, None)) is not None
    ]
    if record_times:
        dependency_time = max(record_times)
        if dependency_time > cutoff:
            raise ValueError("raw dependency knowledge time exceeds PIT cutoff")
        dependency_times[identity] = max(
            dependency_times.get(identity, dependency_time),
            dependency_time,
        )
    used.add((domain, identity))
    return identity


def _batch_binding(batch, domain, suppliers, versions, now):
    supplier = suppliers.get((domain, batch.source_id, batch.source_version))
    if (
        supplier is None
        or supplier[0].model_dump(mode="json") != batch.grant.model_dump(mode="json")
        or batch.provenance != "contemporaneous"
        or batch.source_time is None
        or not batch.source_time <= batch.as_of <= now
        or batch.calendar_version != versions.calendar
        or batch.calendar.version != versions.calendar
    ):
        raise ValueError("raw batch source/config/time binding mismatch")
    if domain == "market" and (
        batch.adjustment_version != versions.adjustment
        or batch.pool_version != versions.pool
    ):
        raise ValueError("raw batch adjustment/pool mismatch")


def _validate_bundle(bundle, suppliers, candidate, now):
    _exact(bundle, BUNDLE_FIELDS)
    now = _now(now)
    versions = _versions(candidate)
    _exact(bundle["candidate"], CANDIDATE_FIELDS)
    methods = _methods(bundle["methods"])
    if bundle["schema"] != INPUT_SCHEMA or dict(bundle["candidate"]) != versions:
        raise ValueError("input candidate binding mismatch")
    if methods != _methods(candidate.get("methods")):
        raise ValueError("input approved methods mismatch")
    cfg = _model(BacktestConfig, bundle["config"])
    assert_compute_budget(cfg)
    if cfg.walk_forward is not None:
        raise ValueError(
            "walk-forward requires an approved actual phase series adapter"
        )
    if cfg.experiment.legs != ("baseline",):
        raise ValueError("AI factor adapter not approved")
    anchor = _model(PitAnchor, bundle["anchor"])
    frames = tuple(
        _model(PitFrame, item) for item in _list(bundle["frames"], maximum=250)
    )
    policy = _model(UniversePolicy, bundle["policy"])
    window = assemble_pit_window(frames, anchor=anchor, policy=policy)
    if (
        type(bundle["publication_sequence"]) is not int
        or bundle["publication_sequence"] != anchor.publication_sequence
        or str(cfg.snapshot_id) != bundle["snapshot_id"]
        or anchor.trade_date != cfg.period.end
        or frames[-1].trade_date != anchor.trade_date
        or frames[-1].publication_sequence != anchor.publication_sequence
    ):
        raise ValueError("input snapshot/anchor/config binding mismatch")
    domains = {
        item.domain: str(item.snapshot_id) for item in cfg.input_manifests.domains
    }
    if set(domains) - {"market", "topic"}:
        raise ValueError("role domain adapter unavailable")
    calendar = frames[-1].market.calendar
    sessions = tuple(
        d for d in calendar.sessions if cfg.period.start <= d <= cfg.period.end
    )
    if (
        not sessions
        or sessions[0] != cfg.period.start
        or tuple(f.trade_date for f in frames) != sessions
        or any(f.market.calendar != calendar for f in frames)
    ):
        raise ValueError("input approved session coverage mismatch")
    providers = _suppliers(suppliers, now, methods)
    receipts = _receipts(bundle["receipts"], providers, now)
    publications = _list(bundle["publications"], maximum=250)
    if len(publications) != len(frames):
        raise ValueError("publication frame coverage mismatch")
    seen, previous_sequence, previous_time = set(), 0, None
    used, required_by_day, dependency_times = set(), [], {}
    for raw, frame, pub in zip(bundle["frames"], frames, publications, strict=True):
        _exact(
            pub,
            {
                "snapshot_id",
                "market_snapshot_id",
                "topic_snapshot_id",
                "publication_sequence",
                "published_at",
                "frame_sha256",
            },
        )
        for field in ("snapshot_id", "market_snapshot_id"):
            if str(UUID(pub[field])) != pub[field] or pub[field] in seen:
                raise ValueError("publication snapshot identity invalid")
            seen.add(pub[field])
        if pub["topic_snapshot_id"] is not None:
            if str(UUID(pub["topic_snapshot_id"])) != pub["topic_snapshot_id"]:
                raise ValueError("topic snapshot identity invalid")
        published = _time(pub["published_at"])
        if (
            type(pub["publication_sequence"]) is not int
            or pub["publication_sequence"] != frame.publication_sequence
            or pub["publication_sequence"] <= previous_sequence
            or (previous_time is not None and published <= previous_time)
            or not frame.market.as_of <= published <= now
            or _digest(pub["frame_sha256"]) != _hash(raw)
        ):
            raise ValueError("publication sequence/hash/time mismatch")
        previous_sequence, previous_time = pub["publication_sequence"], published
        market = frame.market
        _batch_binding(market, "market", providers, cfg.input_manifests.versions, now)
        if validate_batch(market, now=now).issues:
            raise ValueError("raw market quality failed")
        required = set()
        required.add(
            _record_receipt(
                market.calendar,
                "calendar",
                market,
                receipts,
                market.as_of,
                used,
                dependency_times,
            )
        )
        for state in market.security_states:
            if (
                state.code is None
                or state.listing_date is None
                or state.is_st is None
                or state.suspended is None
                or state.is_delisting is None
                or state.board_category == "synthetic_test"
            ):
                raise ValueError("raw historical identity/state fields missing")
            required.add(
                _record_receipt(
                    state,
                    "state",
                    market,
                    receipts,
                    market.as_of,
                    used,
                    dependency_times,
                )
            )
        for bar in market.bars:
            if any(
                getattr(bar, field) is None
                for field in ("open", "high", "low", "close", "volume", "amount")
            ):
                raise ValueError("raw required OHLC volume/amount missing")
            required.add(
                _record_receipt(
                    bar,
                    "market",
                    market,
                    receipts,
                    market.as_of,
                    used,
                    dependency_times,
                )
            )
        if market.limit_up_events:
            raise ValueError("limit-event receipt adapter not approved")
        topic = frame.topic
        if (topic is None) != (pub["topic_snapshot_id"] is None):
            raise ValueError("topic domain/snapshot mismatch")
        if ("topic" in domains or policy.topic_ids) and topic is None:
            raise ValueError("required topic inputs missing")
        if topic is not None:
            _batch_binding(topic, "topic", providers, cfg.input_manifests.versions, now)
            if topic.calendar != calendar or topic.as_of > published:
                raise ValueError("topic calendar/publication mismatch")
            topic_quality = validate_topic_batch(topic, now=now)
            if (
                topic_quality.issues
                or not topic.topics
                or not topic.coverage_cells
                or topic_quality.coverage.expected == 0
                or topic_quality.coverage.missing
            ):
                raise ValueError("topic quality/completeness validation failed")
            records = (
                *topic.topics,
                *topic.memberships,
                *topic.coverage_cells,
                topic.page_manifest,
            )
            required.add(
                _record_receipt(
                    topic.calendar,
                    "calendar",
                    topic,
                    receipts,
                    topic.as_of,
                    used,
                    dependency_times,
                )
            )
            for record in records:
                required.add(
                    _record_receipt(
                        record,
                        "topic",
                        topic,
                        receipts,
                        topic.as_of,
                        used,
                        dependency_times,
                    )
                )
            if (
                any(
                    c.status != "complete" or c.complete_as_of > topic.as_of
                    for c in topic.coverage_cells
                )
                or any(t.available_at > topic.as_of for t in topic.topics)
                or any(m.available_at > topic.as_of for m in topic.memberships)
            ):
                raise ValueError("topic historical coverage unavailable")
        required_by_day.append(required)
    last = publications[-1]
    if (
        last["snapshot_id"] != bundle["snapshot_id"]
        or last["market_snapshot_id"] != domains["market"]
        or last["topic_snapshot_id"] != domains.get("topic")
    ):
        raise ValueError("input manifest domain binding mismatch")
    benchmark = _benchmark(bundle["benchmark_closes"])
    if benchmark:
        for day, close in bundle["benchmark_closes"].items():
            identity = _hash({"trade_date": day, "close": close})
            item = receipts.get(("benchmark", identity))
            if item is None:
                raise ValueError("benchmark raw reception missing")
            cutoff = next(
                (f.market.as_of for f in frames if f.trade_date.isoformat() == day),
                frames[0].market.as_of,
            )
            if _time(item["received_at"]) > cutoff:
                raise ValueError("benchmark actually received after cutoff")
            used.add(("benchmark", identity))
        if any(frame.trade_date not in benchmark for frame in frames):
            raise ValueError("benchmark session coverage missing")
    if set(receipts) != used:
        raise ValueError("unbound raw reception evidence")
    archive = _exact(
        bundle["factor_archive"], {"schema", "method_version", "frames", "lineage"}
    )
    if archive["schema"] != FACTORS_SCHEMA or cfg.parameters.factor_id != "FAC-R01":
        raise ValueError("factor adapter not approved")
    version = _text(archive["method_version"])
    if methods.get(cfg.parameters.factor_id) != version:
        raise ValueError("factor approved method version mismatch")
    factors = tuple(
        _model(FactorFrame, item) for item in _list(archive["frames"], maximum=250)
    )
    lineage = _list(archive["lineage"], maximum=250)
    if len(factors) != len(frames) or len(lineage) != len(frames):
        raise ValueError("factor archive coverage mismatch")
    receipts_by_digest = {}
    for (_, identity), item in receipts.items():
        receipts_by_digest.setdefault(identity, []).append(item)
    for frame, factor, line, pub, day, required in zip(
        frames,
        factors,
        lineage,
        publications,
        window.days,
        required_by_day,
        strict=True,
    ):
        _exact(
            line,
            {
                "trade_date",
                "available_at",
                "market_snapshot_id",
                "topic_snapshot_id",
                "publication_sequence",
                "source_refs",
            },
        )
        cutoff = (
            min(frame.market.as_of, frame.topic.as_of)
            if frame.topic
            else frame.market.as_of
        )
        refs = _list(line["source_refs"])
        hashes = {_digest(x) for x in refs}
        if (
            factor.factor_id != cfg.parameters.factor_id
            or factor.metric not in METRICS
            or factor.window != f"{METRICS[factor.metric]}d"
            or factor.unit != "ratio"
            or factor.trade_date != frame.trade_date
            or line["trade_date"] != frame.trade_date.isoformat()
            or not _time(line["available_at"]) <= cutoff
            or any(
                line[k] != pub[k]
                for k in (
                    "market_snapshot_id",
                    "topic_snapshot_id",
                    "publication_sequence",
                )
            )
            or len(hashes) != len(refs)
            or not required <= hashes
            or not hashes <= {key[1] for key in receipts}
        ):
            raise ValueError("factor archive source/snapshot/time binding mismatch")
        for identity in hashes:
            sources = receipts_by_digest[identity]
            factor_available = _time(line["available_at"])
            if (
                any(_time(item["received_at"]) > factor_available for item in sources)
                or dependency_times.get(identity, factor_available) > factor_available
            ):
                raise ValueError(
                    "factor source actually unavailable at materialization"
                )
            if any(
                providers[(item["domain"], item["source_id"], item["source_version"])][
                    1
                ].get(cfg.parameters.factor_id)
                != version
                for item in sources
            ):
                raise ValueError("factor source method not approved")
        points = {point.security_id: point for point in factor.points}
        if any(
            security.security_id not in points
            or not isinstance(points[security.security_id].value, Decimal)
            or not points[security.security_id].value.is_finite()
            for security in day.securities
        ):
            raise ValueError("required materialized factor absent")
    return cfg, anchor, frames, factors, policy, benchmark


def _benchmark(value):
    if not isinstance(value, Mapping) or len(value) > 1000:
        raise ValueError("benchmark schema invalid")
    result = {}
    for raw_day, raw_close in value.items():
        if type(raw_close) is not str:
            raise ValueError("benchmark price must be an exact decimal string")
        try:
            day, close = date.fromisoformat(raw_day), Decimal(raw_close)
        except (ValueError, TypeError, InvalidOperation):
            raise ValueError("benchmark session/price invalid") from None
        if day.isoformat() != raw_day or not close.is_finite() or close <= 0:
            raise ValueError("benchmark price invalid")
        result[day] = close
    return result


@dataclass(frozen=True)
class FrozenInputBundle:
    """Bytes are the sole stored state; properties return newly parsed models."""

    canonical: bytes

    @property
    def input_sha256(self) -> str:
        return sha256(self.canonical).hexdigest()

    @property
    def config(self) -> BacktestConfig:
        return _model(BacktestConfig, self.to_dict()["config"])

    def to_dict(self) -> dict:
        return strict_json(self.canonical)


def freeze_input_bundle(bundle, suppliers, candidate, now) -> FrozenInputBundle:
    """Validate semantics, then detach completely from caller-owned collections."""
    encoded = canonical_json(bundle)
    detached = strict_json(encoded)
    _validate_bundle(detached, suppliers, candidate, now)
    return FrozenInputBundle(encoded)


def write_input_archive(asset_root: Path, bundle: FrozenInputBundle) -> dict:
    """Write once using secure_output; raw data never gets a fixture fallback."""
    if not isinstance(bundle, FrozenInputBundle):
        raise ValueError("validated frozen bundle required")
    path = bundle.input_sha256 + ".json"
    with secure_output(Path(asset_root) / path) as stream:
        stream.write(bundle.canonical)
    content = read_asset(Path(asset_root), path, MAX_BYTES)
    if content != bundle.canonical:
        raise ValueError("immutable archive integrity invalid")
    return {"path": path, "sha256": bundle.input_sha256}


def read_input_archive(asset_root: Path, ref: Mapping) -> dict:
    """Read a bounded, private digest-named archive; this alone grants no trust."""
    _exact(ref, {"path", "sha256"})
    identity = _digest(ref["sha256"])
    if ref["path"] != identity + ".json":
        raise ValueError("immutable archive reference invalid")
    data = read_asset(Path(asset_root), ref["path"], MAX_BYTES)
    value = strict_json(data)
    if data != canonical_json(value) or sha256(data).hexdigest() != identity:
        raise ValueError("immutable archive digest invalid")
    return value


def materialize_relative_strength(
    frames: Sequence[PitFrame],
    *,
    metric: str,
    benchmark_closes: Mapping[date, Decimal],
) -> tuple[FactorFrame, ...]:
    """Arithmetic only, after authorized input validation; no invented scoring.

    N-session return needs N+1 explicit approved sessions, all raw security and
    benchmark prices, and same-snapshot adjustment factors. Do not infer days,
    fill gaps, reuse a later frame's history, or materialize unsupported factors.
    """
    sessions_back = METRICS.get(metric)
    if sessions_back is None or not frames:
        raise ValueError("relative-strength method unsupported or inputs missing")
    result = []
    for frame in frames:
        batch = frame.market
        sessions = batch.calendar.sessions
        if frame.trade_date not in sessions:
            raise ValueError("relative-strength calendar unavailable")
        index = sessions.index(frame.trade_date)
        if index < sessions_back:
            raise ValueError("relative-strength historical sessions missing")
        wanted = sessions[index - sessions_back : index + 1]
        if any(d not in benchmark_closes for d in wanted):
            raise ValueError("relative-strength benchmark prices missing")
        benchmark_return = period_return(
            benchmark_closes[wanted[0]], benchmark_closes[wanted[-1]]
        )
        points = []
        for sid in batch.expected_security_ids:
            prices = []
            for day in wanted:
                bars = [
                    b
                    for b in batch.bars
                    if b.security_id == sid and b.trade_date == day
                ]
                if len(bars) != 1 or bar_issues(bars[0], batch):
                    raise ValueError("relative-strength raw history missing or invalid")
                prices.append(adjusted_price(bars[0].close, bars[0].adjustment_factor))
            points.append(
                FactorPoint(
                    security_id=sid,
                    value=relative_return(
                        period_return(prices[0], prices[-1]), benchmark_return
                    ),
                )
            )
        result.append(
            FactorFrame(
                trade_date=frame.trade_date,
                factor_id="FAC-R01",
                metric=metric,
                window=f"{sessions_back}d",
                unit="ratio",
                points=tuple(points),
            )
        )
    return tuple(result)


def validate_input_run(
    bundle: dict,
    run: dict,
    suppliers: dict,
    candidate: Mapping,
    now: datetime,
) -> dict:
    """Local evidence validator: authenticate externally, then reexecute kernel.

    Returns only digest/identity/timing/count summaries. Never call from GET.
    A matching hash proves integrity, NOT that a claimed result was executed.
    """
    now = _now(now)
    encoded = canonical_json(bundle)
    bundle = strict_json(encoded)
    _exact(run, RUN_FIELDS)
    if run["schema"] != RUN_SCHEMA:
        raise ValueError("production run schema invalid")
    cfg, anchor, frames, factors, policy, benchmark = _validate_bundle(
        bundle,
        suppliers,
        candidate,
        now,
    )
    if (
        run["candidate"] != bundle["candidate"]
        or run["snapshot_id"] != bundle["snapshot_id"]
        or type(run["publication_sequence"]) is not int
        or run["publication_sequence"] != bundle["publication_sequence"]
        or _digest(run["config_sha256"]) != _hash(bundle["config"])
        or _digest(run["input_sha256"]) != sha256(encoded).hexdigest()
    ):
        raise ValueError("production run input/config/candidate binding mismatch")
    start, finish = _time(run["started_at"]), _time(run["finished_at"])
    if not _time(bundle["publications"][-1]["published_at"]) <= start <= finish <= now:
        raise ValueError("production run execution time invalid")
    # Receipt/use permissions are checked again for the actual claimed run.
    _suppliers(suppliers, start, bundle["methods"])
    _suppliers(suppliers, finish, bundle["methods"])
    try:
        validate_result(run["result"], cfg)
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("production result contract invalid") from exc
    actual = run_backtest(
        cfg,
        frames,
        factors,
        anchor=anchor,
        policy=policy,
        benchmark_closes=benchmark or None,
    ).model_dump(mode="json")
    if run["result"].get("schema_version") == RESEARCH_RESULT_SCHEMA_VERSION:
        from dragon_radar.research.materialization import materialize_result

        actual = materialize_result(
            actual,
            cfg,
            frames,
            {cfg.parameters.factor_id: factors},
            anchor=anchor,
            policy=policy,
            publications=bundle["publications"],
            computed_at=finish,
        )
    if _digest(run["result_sha256"]) != _hash(run["result"]) or canonical_json(
        actual
    ) != canonical_json(run["result"]):
        raise ValueError("production actual deterministic result mismatch")
    return {
        "input_sha256": run["input_sha256"],
        "config_sha256": run["config_sha256"],
        "result_sha256": run["result_sha256"],
        "snapshot_id": run["snapshot_id"],
        "publication_sequence": run["publication_sequence"],
        "started_at": run["started_at"],
        "finished_at": run["finished_at"],
        "sessions": len(frames),
        "factor_id": cfg.parameters.factor_id,
        "method_version": bundle["factor_archive"]["method_version"],
    }


class ProductionResearchCompute:
    """Explicit worker callable; no unsigned inputs or default production policy.

    ``policy`` may be a loader so externally managed revocations are reread for
    each run. ``expected_code`` must calculate the current local source identity,
    never echo a proof's claimed version. ``candidate`` is detached at creation.
    Kernel results retain their existing shape; run artifacts are separately
    written to ``output_root`` under their canonical SHA256 identities.
    """

    def __init__(
        self,
        *,
        asset_root: Path,
        record_path: str,
        suppliers_path: Path,
        output_root: Path,
        policy: TrustPolicy | Callable[[], TrustPolicy],
        candidate: Mapping,
        context: Mapping,
        expected_code: Callable[[], str],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        publication_sequence: Callable[[UUID], int] | None = None,
        result_version: str = "v1",
    ):
        self.asset_root = Path(asset_root)
        self.record_path = _text(record_path)
        self.suppliers_path = Path(suppliers_path)
        self.output_root = Path(output_root)
        if not all(
            p.is_absolute()
            for p in (
                self.asset_root,
                self.suppliers_path,
                self.output_root,
            )
        ):
            raise ValueError("production roots must be explicit absolute paths")
        if not isinstance(policy, TrustPolicy) and not callable(policy):
            raise ValueError("production trust policy required")
        if not callable(expected_code) or not callable(clock):
            raise ValueError("current production code/time checks required")
        _versions(candidate)
        _methods(candidate.get("methods"))
        _exact(context, SCOPE_FIELDS)
        for value in context.values():
            _text(value)
        self._candidate = canonical_json(candidate)
        self._context = canonical_json(context)
        self.policy = policy
        self.expected_code = expected_code
        self.clock = clock
        self.publication_sequence = publication_sequence
        if result_version not in ("v1", "v2"):
            raise ValueError("unsupported research result version")
        self.result_version = result_version

    @property
    def compute_contract_sha256(self):
        from dragon_radar.research.cache_contract import compute_contract

        return compute_contract(self.result_version)

    def prepare_history(self, config):
        """Authenticate the exact archive used by compute, independently of output."""
        from dragon_radar.research.materialization import authorized_frame_mapping

        try:
            frozen, suppliers, candidate, now, _ = self._authenticated()
            bundle = frozen.to_dict()
            cfg, anchor, frames, _, policy, _ = _validate_bundle(
                bundle, suppliers, candidate, now
            )
            if cfg.canonical_json() != config.canonical_json():
                raise ValueError("trusted history config mismatch")
            return authorized_frame_mapping(
                frames,
                anchor=anchor,
                publications=bundle["publications"],
                policy=policy,
            )
        except (EvidenceError, ValueError, TypeError, KeyError, OSError):
            raise ResearchInputsUnavailable(
                "trusted frame mapping unavailable"
            ) from None

    def _authenticated(self):
        now = _now(self.clock())
        candidate = strict_json(self._candidate)
        if candidate["code"] != self.expected_code():
            raise ValueError("current production candidate source mismatch")
        policy = self.policy() if callable(self.policy) else self.policy
        verified = verify_evidence(
            self.record_path,
            self.asset_root,
            policy,
            _versions(candidate),
            strict_json(self._context),
            now,
        )
        if verified.record.gate_id != "real_input":
            raise ValueError("production input proof gate mismatch")
        payload = verified.record.payload
        # A bootstrap input proof may omit a run. It can authorize computation,
        # but cannot pass the real_input gate without an independently bound run.
        if set(payload) not in ({"bundle", "supplier"}, {"bundle", "run", "supplier"}):
            raise ValueError("production input proof payload invalid")
        bundle = strict_json(verified.materials[payload["bundle"]])
        suppliers_raw = verified.materials[payload["supplier"]]
        configured = read_asset(
            self.suppliers_path.parent,
            self.suppliers_path.name,
            MAX_BYTES,
        )
        if configured != suppliers_raw:
            raise ValueError("configured supplier differs from signed supplier")
        suppliers = strict_json(suppliers_raw)
        frozen = freeze_input_bundle(bundle, suppliers, candidate, now)
        return frozen, suppliers, candidate, now, verified.record_sha256

    def prepare(self) -> FrozenInputBundle:
        """Authenticate signed input semantics before explicit CLI injection."""
        try:
            return self._authenticated()[0]
        except (EvidenceError, ValueError, TypeError, KeyError, OSError):
            raise ResearchInputsUnavailable(
                "production input authorization unavailable"
            ) from None

    def __call__(
        self,
        snapshot: PublishedSnapshot,
        config: BacktestConfig,
        checkpoint: Callable[[], None],
    ) -> Mapping:
        try:
            frozen, suppliers, candidate, started, proof_hash = self._authenticated()
            bundle = frozen.to_dict()
            cfg, anchor, frames, factors, policy, benchmark = _validate_bundle(
                bundle,
                suppliers,
                candidate,
                started,
            )
            if (
                not isinstance(snapshot, PublishedSnapshot)
                or not isinstance(config, BacktestConfig)
                or config.canonical_json() != cfg.canonical_json()
                or self.publication_sequence is None
                or any(
                    self.publication_sequence(UUID(pub["snapshot_id"]))
                    != pub["publication_sequence"]
                    for pub in bundle["publications"]
                )
                or snapshot.publication.status != "published"
                or str(snapshot.id) != bundle["snapshot_id"]
                or snapshot.trade_date != anchor.trade_date
                or snapshot.published_at
                != _time(bundle["publications"][-1]["published_at"])
                or str(snapshot.market_snapshot.id)
                != bundle["publications"][-1]["market_snapshot_id"]
                or snapshot.publication.market_snapshot_id
                != snapshot.market_snapshot.id
                or (
                    str(snapshot.publication.topic_snapshot_id)
                    if snapshot.publication.topic_snapshot_id
                    else None
                )
                != bundle["publications"][-1]["topic_snapshot_id"]
                or snapshot.batch.model_dump(mode="json")
                != frames[-1].market.model_dump(mode="json")
            ):
                raise ValueError("anchored PublishedSnapshot/config mismatch")
        except (EvidenceError, ValueError, TypeError, KeyError, OSError):
            raise ResearchInputsUnavailable(
                "production inputs do not match this run"
            ) from None

        def authorized_checkpoint():
            checkpoint()
            try:
                _suppliers(suppliers, _now(self.clock()), bundle["methods"])
            except (ValueError, TypeError):
                raise ResearchInputsUnavailable(
                    "production grant expired during run"
                ) from None

        # Budget/cancellation exceptions propagate unchanged into the existing
        # worker's lease/failure protocol. Validation never swallows them.
        assert_compute_budget(config)
        authorized_checkpoint()
        actual = run_backtest(
            cfg,
            frames,
            factors,
            anchor=anchor,
            policy=policy,
            benchmark_closes=benchmark or None,
            checkpoint=authorized_checkpoint,
        ).model_dump(mode="json")
        authorized_checkpoint()
        try:
            current, _, _, finished, current_proof = self._authenticated()
            authorized_checkpoint()
            if (
                current.input_sha256 != frozen.input_sha256
                or current_proof != proof_hash
            ):
                raise ValueError("production proof changed during computation")
            if finished < started:
                raise ValueError("production execution clock went backwards")
            if self.result_version == "v2":
                from dragon_radar.research.materialization import materialize_result

                actual = materialize_result(
                    actual,
                    cfg,
                    frames,
                    {cfg.parameters.factor_id: factors},
                    anchor=anchor,
                    policy=policy,
                    publications=bundle["publications"],
                    computed_at=finished,
                )
            validate_result(actual, cfg)
            artifact = {
                "schema": RUN_SCHEMA,
                "candidate": bundle["candidate"],
                "snapshot_id": bundle["snapshot_id"],
                "publication_sequence": bundle["publication_sequence"],
                "config_sha256": _hash(bundle["config"]),
                "input_sha256": frozen.input_sha256,
                "result_sha256": _hash(actual),
                "started_at": started.isoformat().replace("+00:00", "Z"),
                "finished_at": finished.isoformat().replace("+00:00", "Z"),
                "result": actual,
            }
            encoded = canonical_json(artifact)
            identity = sha256(encoded).hexdigest()
            authorized_checkpoint()
            with secure_output(self.output_root / (identity + ".json")) as stream:
                stream.write(encoded)
                authorized_checkpoint()
            if (
                read_input_archive(
                    self.output_root,
                    {"path": identity + ".json", "sha256": identity},
                )
                != artifact
            ):
                raise ValueError("production run archive integrity invalid")
            authorized_checkpoint()
        except (EvidenceError, ValueError, TypeError, KeyError, OSError):
            raise ResearchInputsUnavailable(
                "production run evidence could not be archived"
            ) from None
        return actual


def load_production_compute(
    request_path: Path,
    *,
    database=None,
    result_version: str = "v1",
) -> ProductionResearchCompute:
    """Owner CLI opt-in; private request schema independent of candidate.json.

    Exact request fields: schema, asset_root, evidence_path, suppliers_path,
    output_root, trust_policy_path, trust_policy_sha256, openssl (absolute or
    null), candidate (EXACT SIX signed version fields), methods (separate
    approved factor versions), context (EXACT subject/purpose/target/audience).
    Schema: dragon-radar-production-research-request/v1. All paths except the
    evidence's root-relative path must be absolute. The policy SHA256 must be
    approved through the user's external trusted configuration channel; it is
    not discovered or accepted from a proof directory. Nothing installs keys.
    The protected request is explicit owner configuration, not proof metadata.
    """
    from dragon_radar.production.cli import (
        CODE_ROOT,
        current_source_identity,
        load_policy,
    )

    path = Path(request_path).absolute()
    request = strict_json(read_asset(path.parent, path.name, MAX_BYTES))
    _exact(
        request,
        {
            "schema",
            "asset_root",
            "evidence_path",
            "suppliers_path",
            "output_root",
            "trust_policy_path",
            "trust_policy_sha256",
            "openssl",
            "candidate",
            "methods",
            "context",
        },
    )
    if request["schema"] != "dragon-radar-production-research-request/v1":
        raise ValueError("production research request schema invalid")
    _exact(request["candidate"], CANDIDATE_FIELDS)
    policy_path = Path(_text(request["trust_policy_path"]))
    if not policy_path.is_absolute():
        raise ValueError("production policy path must be absolute")
    identity = _digest(request["trust_policy_sha256"])
    openssl = None if request["openssl"] is None else Path(_text(request["openssl"]))
    if openssl is not None and not openssl.is_absolute():
        raise ValueError("production verifier path must be absolute")

    def sequence_for(identity: UUID) -> int:
        from sqlalchemy import select

        from dragon_radar.publication.tables import publications

        if database is None:
            raise ValueError("authorized local publication database is required")
        with database.engine.connect() as connection:
            sequence = connection.execute(
                select(publications.c.publication_sequence).where(
                    publications.c.id == identity,
                    publications.c.status == "published",
                )
            ).scalar_one_or_none()
        if type(sequence) is not int:
            raise ValueError("actual published sequence unavailable")
        return sequence

    compute = ProductionResearchCompute(
        asset_root=Path(_text(request["asset_root"])),
        record_path=request["evidence_path"],
        suppliers_path=Path(_text(request["suppliers_path"])),
        output_root=Path(_text(request["output_root"])),
        policy=lambda: load_policy(policy_path, identity, openssl),
        candidate=dict(request["candidate"])
        | {"methods": _methods(request["methods"])},
        context=request["context"],
        expected_code=lambda: current_source_identity(CODE_ROOT),
        publication_sequence=sequence_for,
        result_version=result_version,
    )
    compute.prepare()
    return compute
