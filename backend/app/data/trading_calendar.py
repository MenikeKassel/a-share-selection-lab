from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import cached_property
from typing import Any, cast

import pandas as pd


class TradingCalendarRangeError(RuntimeError):
    """Raised when the bundled exchange calendar cannot cover the requested date."""


@dataclass
class AShareTradingCalendar:
    """Authoritative XSHG session calendar used by the formal freshness gate."""

    calendar_name: str = "XSHG"
    market_data_ready_at: time = time(16, 30)

    @cached_property
    def calendar(self) -> Any:
        import exchange_calendars as xcals

        return xcals.get_calendar(self.calendar_name)

    def expected_latest_trade_date(self, now: datetime) -> date:
        candidate = now.date()
        if now.timetz().replace(tzinfo=None) < self.market_data_ready_at:
            candidate -= timedelta(days=1)
        try:
            session = self.calendar.date_to_session(
                pd.Timestamp(candidate),
                direction="previous",
            )
        except (ValueError, IndexError) as error:
            raise TradingCalendarRangeError(
                "XSHG 交易日历无法覆盖当前日期；请更新 exchange-calendars，"
                "或在选股请求中显式传入 expected_trade_date。"
            ) from error
        return cast(date, session.date())
