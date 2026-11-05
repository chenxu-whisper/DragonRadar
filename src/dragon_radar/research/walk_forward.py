"""Walk-forward rolling validation and five-regime conditional weighting.

Implemented in Task 8. Fold splits keep train/test ranges strictly disjoint;
thresholds and weights fit only on training folds and freeze before test
evaluation. Regime weighting reads the EX16 sentiment research phase visible
at each PIT rebalance day and degrades to no weighting when the phase is
missing. Pure functions only.
"""
