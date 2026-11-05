"""Deterministic A-share execution rule ``simulate_fill`` (Task 3).

Pure function, Decimal arithmetic, no IO or clock. Encodes T+1, suspension,
limit-up/limit-down blocking, delisting and cash/position guards, returning a
fill or a structured rejection with a closed-set reason code.
"""
