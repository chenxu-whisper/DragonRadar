"""B7 deep wire acceptance against real deterministic kernel artifacts."""

import copy
import importlib
import json

import pytest
from test_reset_backtest import (
    _eng_config,
    _run_wf,
    _wf_config,
    golden_engine_result_json,
)


def actual_result():
    cfg = _eng_config()
    return cfg, json.loads(golden_engine_result_json())


def validate(payload, cfg):
    module = importlib.import_module("dragon_radar.research.result_contracts")
    return module.validate_result(payload, cfg)


def test_valid_actual_kernel_result_roundtrips():
    cfg, payload = actual_result()
    assert validate(payload, cfg).config_hash == cfg.config_hash


@pytest.mark.parametrize(
    "path,value",
    [
        (("metrics", "total_return"), True),
        (("metrics", "total_return"), 0.1),
        (("metrics", "final_equity"), "NaN"),
        (("metrics", "total_return"), "0.00"),
        (("metrics", "total_cost"), "999.00"),
        (("metrics", "initial_equity"), "0.00"),
        (("metrics", "max_drawdown"), "1.000001"),
        (("metrics", "excess_return"), "99.000000"),
        (("metrics", "invented_gain"), "0.100000"),
        (("versions", "execution_method"), "magic-v1"),
        (("versions", "factor_id"), "FAC-R02"),
        (("config_hash",), "a" * 64),
        (("snapshot_id",), "22222222-2222-4222-8222-222222222222"),
        (("quality", 0, "events"), [{"trade_date": "2025-01-06", "code": "magic"}]),
        (("equity", 0, "equity"), "99999.00"),
        (("equity", 1, "trade_date"), "1900-01-01"),
        (("trades", 0, "quantity"), True),
        (("trades", 0, "fill_price"), "10.00"),
        (("trades", 0, "net_cash_delta"), "1.00"),
        (("trades", 0, "signal_date"), "2099-01-01"),
        (("ablation", "ai_variant", "metrics"), {}),
        (("ablation", "ai_variant", "uplift"), "1.000000"),
        (("baseline", "cost_sensitivity", 1, "cost_multiplier"), "2"),
    ],
)
def test_result_rejects_corrupt_materialized_claim(path, value):
    cfg, original = actual_result()
    payload = copy.deepcopy(original)
    node = payload
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(ValueError):
        validate(payload, cfg)


def test_manifest_domain_order_is_not_current_publication_binding():
    cfg, payload = actual_result()
    payload["manifests"]["domains"].reverse()
    assert validate(payload, cfg).config_hash == cfg.config_hash
    payload["manifests"]["versions"]["pool"] = "other-pool"
    with pytest.raises(ValueError):
        validate(payload, cfg)


def test_empty_trades_is_not_missing_trades():
    cfg, payload = actual_result()
    del payload["trades"]
    with pytest.raises(ValueError):
        validate(payload, cfg)


def test_anchored_actual_folds_roundtrip():
    cfg = _wf_config()
    payload = _run_wf().model_dump(mode="json")
    assert len(validate(payload, cfg).folds) == cfg.walk_forward.folds


@pytest.mark.parametrize(
    "path,value",
    [
        (("walk_forward", "layout"), "rolling-refit-invented-v1"),
        (("walk_forward", "folds", 1, "test_start"), "2025-01-01"),
        (("walk_forward", "regime_map", "fit_method"), "current-pool-fit-v1"),
        (("walk_forward", "regime_map", "train_end"), "2099-01-01"),
        (("walk_forward", "aggregate_metrics", "total_return"), "0.123456"),
        (("folds", 0, "fold_index"), 9),
    ],
)
def test_folds_reject_tampered_layout_and_aggregate(path, value):
    cfg = _wf_config()
    payload = _run_wf().model_dump(mode="json")
    node = payload
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(ValueError):
        validate(payload, cfg)
