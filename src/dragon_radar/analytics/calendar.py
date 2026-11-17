"""Calendar-position windows, without inferred sessions or shifted start dates."""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date

from dragon_radar.domain.models import ExchangeCalendar


@dataclass(frozen=True)
class TradingCalendar:
    """Wrap the batch's explicit calendar; windows return inclusive date tuples."""

    calendar: ExchangeCalendar

    def n_day_window(self, end: date, n: int) -> tuple[date, ...]:
        """N return offsets need N+1 sessions. Roll end back, never shift start."""
        if type(n) is not int or not 1 <= n <= 250:
            raise ValueError("n must be an integer from 1 to 250 (not bool)")
        sessions = self.calendar.sessions
        stop = bisect_right(sessions, end)
        if stop < n + 1:
            raise ValueError("insufficient calendar history for requested window")
        return sessions[stop - n - 1 : stop]

    def custom_window(self, start: date, end: date) -> tuple[date, ...]:
        """Roll start forward/end backward; reversed or empty windows fail."""
        if start > end:
            raise ValueError("reversed window")
        sessions = self.calendar.sessions
        window = sessions[bisect_left(sessions, start) : bisect_right(sessions, end)]
        if not window:
            raise ValueError("empty calendar window")
        return window
