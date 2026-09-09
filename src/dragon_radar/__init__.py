"""Offline, deterministic market-data contracts and analytics."""


def main() -> int:
    """Lazy console entry point; importing the package never connects to a DB."""
    from dragon_radar.cli import main as cli_main

    return cli_main()
