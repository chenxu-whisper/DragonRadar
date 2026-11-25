"""Actual anchored materialization, not synthetic health from coverage."""

import copy
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from test_b7_result_contracts import actual_result
from test_reset_backtest import (
    ENG_DAYS,
    ENG_FLAT,
    ENG_VALUES_BY_DAY,
    _eng_anchor,
    _eng_frames,
    _factor_frames,
)

from dragon_radar.research.result_contracts import canonical, validate_result


def inputs():
    from hashlib import sha256

    cfg, result = actual_result()
    cfg = cfg.model_copy(
        update={
            "input_manifests": cfg.input_manifests.model_copy(
                update={
                    "domains": tuple(
                        d for d in cfg.input_manifests.domains if d.domain == "market"
                    )
                }
            )
        }
    )
    frames = _eng_frames({day: dict(ENG_FLAT) for day in ENG_DAYS})
    from dragon_radar.research.engine import run_backtest

    result = run_backtest(
        cfg, frames, _factor_frames(ENG_VALUES_BY_DAY), anchor=_eng_anchor()
    ).model_dump(mode="json")
    pubs = [
        {
            "snapshot_id": str(uuid4()),
            "market_snapshot_id": str(uuid4()),
            "topic_snapshot_id": None,
            "publication_sequence": frame.publication_sequence,
            "published_at": frame.market.as_of.isoformat(),
            "frame_sha256": sha256(
                canonical(frame.model_dump(mode="json"))
            ).hexdigest(),
        }
        for frame in frames
    ]
    pubs[-1]["snapshot_id"] = str(cfg.snapshot_id)
    pubs[-1]["market_snapshot_id"] = str(cfg.input_manifests.domains[0].snapshot_id)
    return cfg, result, frames, pubs


def materialized():
    from dragon_radar.research.materialization import materialize_result

    cfg, result, frames, pubs = inputs()
    return cfg, materialize_result(
        result,
        cfg,
        frames,
        {"FAC-R01": _factor_frames(ENG_VALUES_BY_DAY)},
        anchor=_eng_anchor(),
        publications=pubs,
        computed_at=datetime(2026, 11, 24, tzinfo=UTC),
    )


def test_actual_health_and_immutable_lineage():
    cfg, payload = materialized()
    validate_result(payload, cfg)
    assert payload["health"]["status"] == "ready"
    assert payload["health"]["report"]["primary_factor_id"] == "FAC-R01"
    assert payload["lineage"]["benchmark"]["status"] == "unavailable"
    assert payload["lineage"]["computed_at"] == "2026-11-24T00:00:00Z"


@pytest.mark.parametrize(
    "part", ["rate", "count", "quantile", "hash", "sequence", "period", "benchmark"]
)
def test_v2_tamper_fails_closed(part):
    cfg, payload = materialized()
    payload = copy.deepcopy(payload)
    factor = payload["health"]["report"]["factors"][0]
    if part == "rate":
        factor["valid_rate"] = "0.000000"
    elif part == "count":
        factor["sample_size"] += 1
    elif part == "quantile":
        factor["quantiles"]["values"][0] = "999.000000"
    elif part == "hash":
        payload["lineage"]["frames"][0]["frame_sha256"] = "broken"
    elif part == "sequence":
        payload["lineage"]["frames"][0]["publication_sequence"] = 999999
    elif part == "period":
        payload["lineage"]["requested_period"]["start"] = "2020-01-01"
    else:
        payload["lineage"]["benchmark"]["source_id"] = "invented"
    with pytest.raises(ValueError):
        validate_result(payload, cfg)


def test_missing_or_wrong_mapping_rejected():
    from dragon_radar.research.materialization import materialize_result

    cfg, result, frames, pubs = inputs()
    for mapping in ([], [{**pubs[0], "frame_sha256": "0" * 64}, *pubs[1:]]):
        with pytest.raises(ValueError):
            materialize_result(
                result,
                cfg,
                frames,
                {"FAC-R01": _factor_frames(ENG_VALUES_BY_DAY)},
                anchor=_eng_anchor(),
                publications=mapping,
                computed_at=datetime(2026, 11, 24, tzinfo=UTC),
            )


def test_benchmark_identity_cannot_promote_unrelated_zero_summary():
    from decimal import Decimal
    from hashlib import sha256

    from dragon_radar.research.materialization import materialize_result

    cfg, result, frames, pubs = inputs()
    closes = {
        frame.trade_date: Decimal(100 + index) for index, frame in enumerate(frames)
    }
    identity = {
        "definition_sha256": "a" * 64,
        "source_id": "test-benchmark",
        "source_version": "test-v1",
        "input_sha256": sha256(
            canonical({day.isoformat(): str(close) for day, close in closes.items()})
        ).hexdigest(),
    }
    with pytest.raises(ValueError, match="benchmark"):
        materialize_result(
            result,
            cfg,
            frames,
            {"FAC-R01": _factor_frames(ENG_VALUES_BY_DAY)},
            anchor=_eng_anchor(),
            publications=pubs,
            computed_at=datetime(2026, 11, 24, tzinfo=UTC),
            benchmark_identity=identity,
            benchmark_closes=closes,
        )


def test_production_loader_exposes_explicit_server_result_contract():
    from inspect import signature

    from dragon_radar.research.production import load_production_compute

    parameter = signature(load_production_compute).parameters.get("result_version")
    assert parameter is not None
    assert parameter.default == "v1"
