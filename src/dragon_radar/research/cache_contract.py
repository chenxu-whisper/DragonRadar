"""Deployment-selected, server-only computation namespace."""

import os
from hashlib import sha256

from dragon_radar.research.result_contracts import (
    RESEARCH_RESULT_SCHEMA_VERSION,
    RESULT_V1,
    canonical,
    require,
)

SCHEMAS = {"v1": RESULT_V1, "v2": RESEARCH_RESULT_SCHEMA_VERSION}


def deployment_result_version():
    """Owner admission and its worker share one explicit server configuration."""
    version = os.environ.get("DRAGON_RESEARCH_RESULT_VERSION", "v2")
    if version not in SCHEMAS:
        raise ValueError("Unsupported research result version")
    return version


def compute_contract(version, *, approved_descriptor=None):
    require(version in SCHEMAS, "unsupported result contract")
    from dragon_radar.research.engine import (
        COST_METHOD,
        EXECUTION_METHOD,
        LEDGER_METHOD,
        SIZING_METHOD,
        VALUATION_METHOD,
    )
    from dragon_radar.research.materialization import HEALTH_METHOD

    return sha256(
        canonical(
            {
                "result": SCHEMAS[version],
                "validation": "deep-ex15-v1",
                "strategy": "factor-top-n-v1",
                "execution": EXECUTION_METHOD,
                "ledger": LEDGER_METHOD,
                "sizing": SIZING_METHOD,
                "valuation": VALUATION_METHOD,
                "cost": COST_METHOD,
                "walk_forward": "walk-forward-anchored-v1",
                "health": HEALTH_METHOD if version == "v2" else None,
                "approved_descriptor": approved_descriptor,
            }
        )
    ).hexdigest()


def cache_key(config, version, contract):
    require(version in SCHEMAS)
    return config.config_hash, SCHEMAS[version], contract
