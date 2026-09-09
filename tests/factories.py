"""Synthetic, offline fixtures. No market provider data."""

from datetime import UTC, date, datetime
from decimal import Decimal

DAYS = (date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 7), date(2026, 9, 8))
NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)


def grant(**changes):
    from dragon_radar.domain.models import SourceGrant

    return SourceGrant(
        **{
            "source_id": "synthetic",
            "approved": True,
            "reference": "synthetic-test-only",
            "starts_at": datetime(2026, 1, 1, tzinfo=UTC),
            "expires_at": datetime(2027, 1, 1, tzinfo=UTC),
            "local_storage": True,
            "local_display": True,
            **changes,
        }
    )


def state(security_id="A", **changes):
    from dragon_radar.domain.models import SecurityState

    return SecurityState(
        **{
            "security_id": security_id,
            "name": f"Synthetic {security_id}",
            "board": "synthetic-board",
            "exchange": "TEST",
            "effective_from": DAYS[0],
            "available_at": datetime(2026, 9, 2, tzinfo=UTC),
            "listing_date": date(2020, 1, 1),
            "suspended": False,
            "is_st": False,
            "source_id": "synthetic",
            "source_version": "s1",
            "pool_version": "p1",
            **changes,
        }
    )


def bar(security_id="A", trade_date=None, **changes):
    from dragon_radar.domain.models import DailyBar

    day = trade_date or DAYS[-1]
    return DailyBar(
        **{
            "security_id": security_id,
            "trade_date": day,
            "close": Decimal("10"),
            "adjustment_factor": Decimal("1"),
            "adjustment_version": "a1",
            "source_id": "synthetic",
            "source_version": "s1",
            "available_at": datetime(day.year, day.month, day.day, 16, tzinfo=UTC),
            "currency": "CNY",
            "price_unit": "currency/share",
            "volume_unit": "share",
            "amount_unit": "currency",
            "volume": Decimal("100"),
            "amount": Decimal("1000"),
            "limit_status": "no_limit",
            **changes,
        }
    )


def batch(**changes):
    from dragon_radar.domain.models import ExchangeCalendar, MarketBatch

    return MarketBatch(
        **{
            "trade_date": DAYS[-1],
            "as_of": NOW,
            "source_id": "synthetic",
            "source_version": "s1",
            "adjustment_version": "a1",
            "calendar_version": "c1",
            "pool_version": "p1",
            "grant": grant(),
            "calendar": ExchangeCalendar(exchange="TEST", version="c1", sessions=DAYS),
            "expected_security_ids": ("A",),
            "security_states": (state(),),
            "bars": tuple(bar(trade_date=day) for day in DAYS),
            "complete_pages": True,
            "provenance": "contemporaneous",
            **changes,
        }
    )
