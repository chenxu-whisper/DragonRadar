"""Explanatory owner window projection; never authority to enable adapters."""

from dragon_radar.research.models import BacktestPeriod


def disabled_capabilities(snapshot_id, period):
    period = BacktestPeriod.model_validate(period.model_dump())

    def disabled(identity, reason):
        return {
            "id": identity,
            "status": "disabled",
            "reason": reason,
            "method_version": None,
            "approval_evidence_sha256": None,
        }

    return {
        "snapshot_id": str(snapshot_id),
        "period": period.model_dump(mode="json"),
        "factors": [
            disabled(factor, "approved_method_unavailable")
            for factor in ("FAC-R01", "FAC-R02", "FAC-R03", "FAC-C01", "FAC-C02")
        ],
        "walk_forward": disabled(
            "walk-forward-anchored-v1", "phase_series_unavailable"
        ),
        "ai_variant": disabled("ai_variant", "real_evaluation_inputs_unavailable"),
    }
