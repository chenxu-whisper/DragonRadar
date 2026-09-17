"""Frozen role rule definitions with canonical JSON identity."""

import hashlib
import json
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

ROLE_RULE_VERSION = "role-v1"

_RAW_RULE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "role-v1": {
        "rule_version": "role-v1",
        "allowed_boards": ["sh_main", "sz_main"],
        "production_activatable": True,
        "decimal_context": {"precision": 34, "rounding": "ROUND_HALF_EVEN"},
        "minimum_listing_sessions": 120,
        "minimum_topic_members": 5,
        "roles": {
            "trend": {"capability": "ready"},
            "core": {"capability": "ready"},
            "emotion": {
                "capability": "simplified",
                "rule_id": "emotion-daily-v1",
            },
            "catchup": {
                "capability": "simplified",
                "rule_id": "catchup-daily-v1",
            },
            "follower": {"capability": "watch_only"},
            "relay": {"capability": "pending_validation"},
        },
    },
    "role-test-v1": {
        "rule_version": "role-test-v1",
        "allowed_boards": ["synthetic_test"],
        "production_activatable": False,
        "decimal_context": {"precision": 34, "rounding": "ROUND_HALF_EVEN"},
        "minimum_listing_sessions": 1,
        "minimum_topic_members": 1,
        "roles": {
            "trend": {"capability": "ready"},
            "core": {"capability": "ready"},
            "emotion": {
                "capability": "simplified",
                "rule_id": "emotion-daily-v1",
            },
            "catchup": {
                "capability": "simplified",
                "rule_id": "catchup-daily-v1",
            },
            "follower": {"capability": "watch_only"},
            "relay": {"capability": "pending_validation"},
        },
    },
}


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


RULE_DEFINITIONS: Mapping[str, Mapping[str, Any]] = MappingProxyType(
    {
        version: _freeze(definition)
        for version, definition in _RAW_RULE_DEFINITIONS.items()
    }
)
del _RAW_RULE_DEFINITIONS


def canonical_rule_json(version: str) -> str:
    """Return the exact canonical JSON archived with each role run."""
    try:
        definition = RULE_DEFINITIONS[version]
    except KeyError as exc:
        raise ValueError(f"unknown role rule version: {version}") from exc

    def mutable_json(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {key: mutable_json(item) for key, item in value.items()}
        if isinstance(value, tuple):
            return [mutable_json(item) for item in value]
        return value

    return json.dumps(
        mutable_json(definition),
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


ROLE_RULE_SHA256 = "1b76b4445512c384c8298045b164e695a75de94d8ffe34e8f6873e196cb1bdb1"
_RULE_SHA256 = MappingProxyType(
    {
        "role-v1": ROLE_RULE_SHA256,
        "role-test-v1": (
            "d137b79e247977a5c0f83c582df3af9f0284955c525c270044782880e7bbeada"
        ),
    }
)


def validate_rule_definition(version: str, canonical_json: str) -> None:
    """Reject content drift for a known immutable rule version."""
    try:
        expected = _RULE_SHA256[version]
    except KeyError as exc:
        raise RuntimeError(f"unknown role rule version: {version}") from exc
    actual = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    if actual != expected:
        raise RuntimeError(
            f"role rule digest mismatch for {version}: {actual} != {expected}"
        )


for _version in RULE_DEFINITIONS:
    validate_rule_definition(_version, canonical_rule_json(_version))
