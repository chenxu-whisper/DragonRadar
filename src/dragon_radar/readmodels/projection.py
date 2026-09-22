"""Request-local market indexes and security-scoped quality projections."""

from collections import defaultdict
from datetime import date

from dragon_radar.analytics.metrics import _index, _observation
from dragon_radar.domain.models import DailyBar, MarketBatch, SecurityState
from dragon_radar.domain.quality import state_at

BarIndex = dict[tuple[str, date], list[DailyBar]]


class MarketProjection:
    """Cache quality lookups against batches with only one security's states."""

    def __init__(self, batch: MarketBatch):
        self.batch = batch
        self.bars: BarIndex = _index(batch)
        self._states: dict[str, list[SecurityState]] = defaultdict(list)
        for state in batch.security_states:
            self._states[state.security_id].append(state)
        self._batches: dict[str, MarketBatch] = {}
        self._effective: dict[tuple[str, date], SecurityState | None] = {}
        self._observations: dict[
            tuple[str, date], tuple[DailyBar | None, tuple[str, ...]]
        ] = {}

    def security_batch(self, security_id: str) -> MarketBatch:
        if security_id not in self._batches:
            self._batches[security_id] = self.batch.model_copy(
                update={
                    "security_states": tuple(self._states.get(security_id, ())),
                }
            )
        return self._batches[security_id]

    def state(self, security_id: str, day: date) -> SecurityState | None:
        key = (security_id, day)
        if key not in self._effective:
            self._effective[key] = state_at(
                self.security_batch(security_id),
                security_id,
                day,
            )
        return self._effective[key]

    def observation(
        self,
        security_id: str,
        day: date,
    ) -> tuple[DailyBar | None, tuple[str, ...]]:
        key = (security_id, day)
        if key not in self._observations:
            self._observations[key] = _observation(
                self.security_batch(security_id),
                self.bars,
                security_id,
                day,
            )
        return self._observations[key]
