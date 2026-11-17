"""Offline supplier-sample diagnostics; no database connection."""

from dragon_radar.ingestion.sample_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
