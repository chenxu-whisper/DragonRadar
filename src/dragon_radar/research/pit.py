"""Point-in-time visible-information set assembly (Task 5).

Pure functions only: given an anchored publication sequence, each rebalance
day sees exclusively data whose ``available_at`` and publication sequence
were visible at that decision point. No database or clock access.
"""
