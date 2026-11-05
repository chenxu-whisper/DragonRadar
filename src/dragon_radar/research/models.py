"""Immutable backtest configuration models, bounded validation, config hash.

Implemented in Task 2. The models are frozen pydantic objects with explicit
finite-Decimal cost assumptions, bounded period/security/fold ranges, and a
deterministic canonical-JSON SHA-256 ``config_hash``.
"""
