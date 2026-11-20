"""Offline sample diagnostics, never a production grant or historical PIT proof."""

import hashlib
import json
from collections import Counter
from datetime import date, datetime
from typing import Literal

from pydantic import Field, field_validator

from dragon_radar.domain.models import ExactDecimal, FrozenModel, MarketBatch
from dragon_radar.domain.quality import (
    bar_issues,
    grant_allows_storage,
    state_at,
    validate_batch,
)

MISSING_QUALITY = frozenset(
    {
        "grant_denied",
        "incomplete_pages",
        "empty_pool",
        "unknown_id",
        "effective_state",
        "effective_limit_up_state",
        "missing_current",
        "insufficient_coverage",
        "missing_price",
    }
)


class SampleRequest(FrozenModel):
    schema_version: Literal["dragon-radar-supplier-sample/v1"] = Field(alias="schema")
    kind: Literal["test", "supplier_sample"]
    batch: MarketBatch
    benchmark_closes: dict[date, ExactDecimal]
    decision_sessions: tuple[date, ...]
    window: Literal[20]

    @field_validator("window", mode="before")
    @classmethod
    def exact_window(cls, value):
        if type(value) is not int:
            raise ValueError("window must be an integer")
        return value

    @field_validator("decision_sessions", "benchmark_closes", mode="before")
    @classmethod
    def explicit_dates(cls, value):
        if not isinstance(value, (dict, tuple, list)):
            raise ValueError("explicit ISO dates required")
        for item in value:
            if type(item) is date:
                continue
            if (
                not isinstance(item, str)
                or date.fromisoformat(item).isoformat() != item
            ):
                raise ValueError("explicit ISO dates required")
        return value

    @field_validator("decision_sessions")
    @classmethod
    def ordered_decisions(cls, value: tuple[date, ...]) -> tuple[date, ...]:
        if not value or tuple(sorted(set(value))) != value:
            raise ValueError("decision sessions must be nonempty, ordered and unique")
        return value


def safe_failure(code: str) -> dict:
    """Only internal constant codes may cross the diagnostic boundary."""
    return {
        "schema": "dragon-radar-supplier-sample-report/v1",
        "status": "fail",
        "production_approved": False,
        "historical_pit_status": "unverified",
        "issues": [{"code": code, "status": "fail", "field": None, "trade_date": None}],
    }


def evaluate_sample(request: SampleRequest, *, now: datetime) -> dict:
    """Evaluate data completeness; original reception proofs remain external."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("operation time requires timezone")
    request = SampleRequest.model_validate(
        request.model_dump(mode="python", by_alias=True)
    )
    batch = request.batch
    issues: list[dict] = []
    seen: set[tuple] = set()

    def add(code: str, *, status="blocked", field=None, day=None):
        key = (code, status, field, day)
        if key not in seen:
            seen.add(key)
            issues.append(
                {
                    "code": code,
                    "status": status,
                    "field": field,
                    "trade_date": day.isoformat() if day is not None else None,
                }
            )

    quality = validate_batch(batch, now=now)
    for issue in quality.issues:
        add(
            issue.code,
            status="blocked" if issue.code in MISSING_QUALITY else "fail",
            day=issue.trade_date,
        )
    if not grant_allows_storage(batch.grant, source_id=batch.source_id, now=now):
        add("storage_grant_denied")
    if request.kind == "supplier_sample" and any(
        part in batch.source_id.lower()
        for part in ("synthetic", "fixture", "mock", "demo")
    ):
        add("test_source_mislabeled", status="fail")

    for state in batch.security_states:
        if state.exchange != "SSE" or state.security_type != "common_stock":
            add("scope_mismatch", status="fail")
        if state.board_category is None:
            add("missing_security_fact", field="board_category")
        elif state.board_category != "sh_main":
            add("scope_mismatch", status="fail")
        for field in ("code", "listing_date", "is_st", "suspended", "is_delisting"):
            if getattr(state, field) is None:
                add("missing_security_fact", field=field)

    sessions = batch.calendar.sessions
    required: set[date] = set()
    for decision in request.decision_sessions:
        if decision not in sessions or decision > batch.trade_date:
            add("decision_calendar", status="fail", day=decision)
            continue
        index = sessions.index(decision)
        if index < request.window:
            add("insufficient_warmup", day=decision)
        required.update(sessions[max(0, index - request.window) : index + 1])
    for day in sorted(required):
        close = request.benchmark_closes.get(day)
        if close is None:
            add("missing_benchmark", day=day)
        elif not close.is_finite() or close <= 0:
            add("invalid_benchmark", status="fail", day=day)
    for day in request.benchmark_closes:
        if day not in sessions or day > batch.trade_date:
            add("benchmark_calendar", status="fail", day=day)

    counts = Counter((bar.security_id, bar.trade_date) for bar in batch.bars)
    indexed = {(bar.security_id, bar.trade_date): bar for bar in batch.bars}
    outside_limits: set[tuple[str, date]] = set()
    for row in batch.bars:
        if (
            row.limit_status != "limited"
            or row.limit_date != row.trade_date
            or any(
                bound is None or not bound.is_finite()
                for bound in (row.limit_down, row.limit_up)
            )
        ):
            continue
        for field in ("open", "high", "low", "close"):
            price = getattr(row, field)
            if (
                price is not None
                and price.is_finite()
                and not row.limit_down <= price <= row.limit_up
            ):
                add(
                    "ohlc_outside_limits",
                    status="fail",
                    field=field,
                    day=row.trade_date,
                )
                outside_limits.add((row.security_id, row.trade_date))
    valid = missing = excluded = 0
    expected = set(batch.expected_security_ids)
    for day in sorted(required):
        for sid in sorted(expected):
            state = state_at(batch, sid, day)
            if state is not None and state.suspended is True:
                excluded += 1
                continue
            row = indexed.get((sid, day))
            if state is None or row is None:
                missing += 1
                add("missing_history", day=day)
                continue
            row_complete = (
                counts[(sid, day)] == 1
                and (sid, day) not in outside_limits
                and not bar_issues(row, batch)
            )
            for field in ("open", "high", "low", "close", "volume", "amount"):
                if getattr(row, field) is None:
                    add("missing_market_fact", field=field, day=day)
                    row_complete = False
            if row.limit_status == "unknown":
                add("missing_limit_metadata", day=day)
                row_complete = False
            if any(
                getattr(state, field) is None
                for field in (
                    "code",
                    "listing_date",
                    "is_st",
                    "suspended",
                    "is_delisting",
                )
            ):
                row_complete = False
            if row_complete:
                valid += 1
            else:
                missing += 1
    status = (
        "fail"
        if any(issue["status"] == "fail" for issue in issues)
        else "blocked"
        if issues
        else "pass"
    )
    return {
        "schema": "dragon-radar-supplier-sample-report/v1",
        "status": status,
        "kind": request.kind,
        "test_only": request.kind == "test",
        "production_approved": False,
        "historical_pit_status": "unverified",
        "input_sha256": hashlib.sha256(
            json.dumps(
                request.model_dump(mode="json", by_alias=True),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest(),
        "coverage": {
            "expected_securities": len(expected),
            "decision_sessions": len(request.decision_sessions),
            "required_sessions": len(required),
            "required_observations": len(required) * len(expected),
            "valid_observations": valid,
            "legal_excluded_observations": excluded,
            "missing_observations": missing,
            "current": quality.coverage.model_dump(),
        },
        "issues": issues,
    }
