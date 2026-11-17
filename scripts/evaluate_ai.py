"""Compatibility CLI for the shared immutable AI evaluation contract."""

import sys

from dragon_radar.ai.evaluation import (  # noqa: F401
    MINIMUM_FACT_SUPPORT_RATE,
    MINIMUM_HUMAN_SAMPLES,
    SCHEMA_VERSION,
    _gate_counts,
    _metrics,
    evaluate_file,
    evaluate_manifest,
    main,
)

if __name__ == "__main__":
    sys.exit(main())
