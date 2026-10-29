"""Task 27 research-method validation subdomain (Slice A).

Pure functions only: no database, HTTP, filesystem, clock, or global state.
See docs/superpowers/specs/2026-10-27-task27-research-validation-design.md.
"""

from dragon_radar.research.factor_health import build_factor_health
from dragon_radar.research.forward_eval import evaluate_all, evaluate_horizon
from dragon_radar.research.models import (
    DEFAULT_CONFIG,
    EVIDENCE_INSUFFICIENT_REAL_SAMPLE,
    EVIDENCE_REAL_SAMPLE_DESCRIPTIVE,
    EVIDENCE_SYNTHETIC_ONLY,
    ComponentCounts,
    DailyComponents,
    InputManifest,
    ResearchValidationError,
    SamplePack,
    ValidationConfig,
    validate_config,
)
from dragon_radar.research.report import (
    assemble_report,
    parse_archived_report,
    serialize_report,
)
from dragon_radar.research.samples import ValidatedPack, validate_pack

__all__ = [
    "DEFAULT_CONFIG",
    "ComponentCounts",
    "DailyComponents",
    "EVIDENCE_INSUFFICIENT_REAL_SAMPLE",
    "EVIDENCE_REAL_SAMPLE_DESCRIPTIVE",
    "EVIDENCE_SYNTHETIC_ONLY",
    "InputManifest",
    "ResearchValidationError",
    "SamplePack",
    "ValidatedPack",
    "ValidationConfig",
    "assemble_report",
    "build_factor_health",
    "evaluate_all",
    "evaluate_horizon",
    "parse_archived_report",
    "serialize_report",
    "validate_config",
    "validate_pack",
]
