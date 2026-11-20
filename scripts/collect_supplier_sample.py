"""Supplier configuration preflight or explicit collection; no database connection."""

from dragon_radar.ingestion.collection_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
