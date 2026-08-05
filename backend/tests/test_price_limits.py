"""Tests for PR 2.4: versioned price-limit rule table."""
from datetime import date
from decimal import Decimal

import pandas as pd
from app.market_rules.price_limits import (
    TICK_SIZE,
    board_of,
    derive_limit_flags,
    limit_prices,
    price_limit_ratio,
)


def test_board_of_classification() -> None:
    assert board_of("000001.SZ") == "main"
    assert board_of("300750.SZ") == "chinext"
    assert board_of("301269.SZ") == "chinext"
    assert board_of("688981.SH") == "star"
    assert board_of("830799.BJ") == "bse"
    assert board_of("430047.BJ") == "bse"


def test_price_limit_ratio_by_board_and_date() -> None:
    # ChiNext: 10% before 2020-08-24, 20% after
    assert price_limit_ratio("300750.SZ", date(2020, 8, 21)) == Decimal("0.10")
    assert price_limit_ratio("300750.SZ", date(2020, 8, 24)) == Decimal("0.20")
    # STAR: 20% from day one (2019-07-22)
    assert price_limit_ratio("688981.SH", date(2019, 7, 22)) == Decimal("0.20")
    assert price_limit_ratio("688981.SH", date(2019, 7, 21)) == Decimal("0.10")
    # BSE: 30% after 2021-11-15
    assert price_limit_ratio("830799.BJ", date(2021, 11, 15)) == Decimal("0.30")
    assert price_limit_ratio("830799.BJ", date(2021, 11, 12)) == Decimal("0.10")
    # Main board stays 10%
    assert price_limit_ratio("000001.SZ", date(2024, 1, 2)) == Decimal("0.10")


def test_price_limit_ratio_st_variant() -> None:
    assert price_limit_ratio("000001.SZ", date(2024, 1, 2), is_st=True) == Decimal("0.05")
    # ST on ChiNext after reform keeps 20% (no separate ST band)
    assert (
        price_limit_ratio("300750.SZ", date(2021, 6, 1), is_st=True) == Decimal("0.20")
    )


def test_limit_prices_rounding() -> None:
    up, down = limit_prices(Decimal("10.00"), Decimal("0.10"))
    assert up == Decimal("11.00")
    assert down == Decimal("9.00")
    # Rounding at fractional ticks
    up, down = limit_prices(Decimal("10.03"), Decimal("0.10"))
    assert up == Decimal("11.03")
    assert down == Decimal("9.03")
    # ST stock: 5% limit but 0.01 tick
    up, down = limit_prices(Decimal("10.00"), Decimal("0.05"))
    assert up == Decimal("10.50")
    assert down == Decimal("9.50")
    # Boundary: 11.033 -> 11.03
    up, _ = limit_prices(Decimal("10.03"), Decimal("0.10"))
    assert up == Decimal("11.03")
    assert Decimal("0.01") == TICK_SIZE


def test_derive_limit_flags_close_at_limit() -> None:
    frame = pd.DataFrame(
        [
            {"date": "2024-01-02", "symbol": "000001.SZ", "open": 10.0,
             "high": 11.0, "low": 9.9, "close": 11.0, "pre_close": 10.0,
             "is_st": False},
            {"date": "2024-01-02", "symbol": "000002.SZ", "open": 10.0,
             "high": 10.5, "low": 9.9, "close": 10.3, "pre_close": 10.0,
             "is_st": False},
            {"date": "2024-01-02", "symbol": "000003.SZ", "open": 10.0,
             "high": 9.6, "low": 9.0, "close": 9.0, "pre_close": 10.0,
             "is_st": False},
            # ChiNext 20%: 11.0 is not a limit (12.0 is)
            {"date": "2024-01-02", "symbol": "300001.SZ", "open": 10.0,
             "high": 12.0, "low": 9.9, "close": 12.0, "pre_close": 10.0,
             "is_st": False},
        ]
    )
    up, down = derive_limit_flags(frame)
    assert up.tolist() == [True, False, False, True]
    assert down.tolist() == [False, False, True, False]


def test_derive_limit_flags_st_uses_five_percent() -> None:
    frame = pd.DataFrame(
        [
            {"date": "2024-01-02", "symbol": "000001.SZ", "open": 10.0,
             "high": 10.5, "low": 9.9, "close": 10.5, "pre_close": 10.0,
             "is_st": True},
        ]
    )
    up, _ = derive_limit_flags(frame)
    assert up.tolist() == [True]


def test_derive_limit_flags_regime_boundary() -> None:
    # ChiNext close at 11.0 (=10% limit) is at-limit before 2020-08-24 but
    # NOT after (20% regime).
    frame = pd.DataFrame(
        [
            {"date": "2020-08-21", "symbol": "300001.SZ", "open": 10.0,
             "high": 11.0, "low": 9.9, "close": 11.0, "pre_close": 10.0,
             "is_st": False},
        ]
    )
    up, _ = derive_limit_flags(frame)
    assert up.tolist() == [True]
    frame["date"] = "2020-08-24"
    up, _ = derive_limit_flags(frame)
    assert up.tolist() == [False]


def test_limit_prices_round_half_up_per_exchange_rule() -> None:
    # PR 4.1: SSE/SZSE round the theoretical limit price to the nearest
    # tick with half-up.  pre_close 10.05 at 10% -> raw_up = 11.055,
    # half-up to the tick gives 11.06 (old ROUND_DOWN gave 11.05).
    up, _ = limit_prices(Decimal("10.05"), Decimal("0.10"))
    assert up == Decimal("11.06")
    # down side: raw_down = 9.045 -> half-up gives 9.05
    _, down = limit_prices(Decimal("10.05"), Decimal("0.10"))
    assert down == Decimal("9.05")
    # pre_close 10.00 -> raw_up 11.00 exact, unchanged
    up, down = limit_prices(Decimal("10.00"), Decimal("0.10"))
    assert (up, down) == (Decimal("11.00"), Decimal("9.00"))
    # pre_close 9.97 -> raw_up = 10.967 -> half-up 10.97
    up, _ = limit_prices(Decimal("9.97"), Decimal("0.10"))
    assert up == Decimal("10.97")
