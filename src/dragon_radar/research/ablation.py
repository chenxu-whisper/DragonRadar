"""Baseline/AI-uplift ablation comparison frame (Task 9).

Assembles same-pool, same-window, same-cost, same-seed legs. Without frozen
real evaluation inputs the AI variant is fixed to
``status=disabled, reason=real_evaluation_inputs_unavailable``; model output
is never fabricated into a return label.
"""
