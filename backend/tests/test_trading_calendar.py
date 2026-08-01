from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.data.trading_calendar import AShareTradingCalendar


def test_exchange_calendar_drives_expected_latest_trade_date() -> None:
    calendar = AShareTradingCalendar()

    after_close = calendar.expected_latest_trade_date(
        datetime(2026, 7, 30, 19, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    before_close = calendar.expected_latest_trade_date(
        datetime(2026, 7, 30, 15, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    spring_festival = calendar.expected_latest_trade_date(
        datetime(2026, 2, 20, 19, tzinfo=ZoneInfo("Asia/Shanghai"))
    )

    assert after_close == date(2026, 7, 30)
    assert before_close == date(2026, 7, 29)
    assert spring_festival == date(2026, 2, 13)
