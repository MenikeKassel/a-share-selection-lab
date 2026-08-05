"""Tests for PR 5: security master (real listing dates) and PIT cutoff."""
import pandas as pd
import pytest
from app.data.pit import asof_for_signal_date
from app.data.security_master import (
    listing_days_for,
    normalise_security_master,
)


def _master_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "000001.SZ",
                "list_date": "1991-04-03",
                "delist_date": pd.NaT,
                "name": "平安银行",
                "market": "主板",
            },
            {
                "symbol": "300750.SZ",
                "list_date": "2018-06-11",
                "delist_date": pd.NaT,
                "name": "宁德时代",
                "market": "创业板",
            },
            {
                "symbol": "688981.SH",
                "list_date": "2020-07-16",
                "delist_date": pd.NaT,
                "name": "中芯国际",
                "market": "科创板",
            },
        ]
    )


def _fallback_universe() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"symbol": "000001.SZ", "list_date": "2018-01-02", "last_observed_date": "2025-12-31"},
            {"symbol": "300750.SZ", "list_date": "2018-06-11", "last_observed_date": "2025-12-31"},
        ]
    )


# ---------------------------------------------------------------------------
# Security master


def test_normalise_security_master_real_listing_dates() -> None:
    master, status = normalise_security_master(_master_frame())

    assert status.real_listing_dates is True
    assert status.listing_date_source == "purchased_security_master"
    assert status.row_count == 3
    assert list(master.columns) == [
        "symbol",
        "list_date",
        "delist_date",
        "exchange",
        "board",
        "security_type",
    ]
    by_symbol = master.set_index("symbol")
    assert by_symbol.loc["000001.SZ", "exchange"] == "SZSE"
    assert by_symbol.loc["000001.SZ", "board"] == "main"
    assert by_symbol.loc["300750.SZ", "board"] == "chinext"
    assert by_symbol.loc["688981.SH", "exchange"] == "SSE"
    assert by_symbol.loc["688981.SH", "board"] == "star"
    assert by_symbol.loc["000001.SZ", "list_date"] == pd.Timestamp("1991-04-03")


def test_normalise_fallback_universe_marked_not_real() -> None:
    master, status = normalise_security_master(_fallback_universe())

    assert status.real_listing_dates is False
    assert status.listing_date_source == "daily_first_observed"
    assert len(master) == 2


def test_listing_days_uses_real_list_date_not_window_start() -> None:
    master, _ = normalise_security_master(_master_frame())
    market = pd.DataFrame(
        [
            {"date": "2024-01-02", "symbol": "000001.SZ"},
            {"date": "2024-01-02", "symbol": "300750.SZ"},
        ]
    )
    listing_days, status = listing_days_for(market, master)

    assert status.real_listing_dates is True
    # 000001.SZ listed 1991-04-03 -> ~11959 days; far beyond a window-start
    # approximation of ~2192 days (2018-01-01).
    days_000001 = listing_days.iloc[0]
    assert days_000001 > 11_000
    # 300750.SZ listed 2018-06-11 -> ~2021 days
    assert 2_000 < listing_days.iloc[1] < 2_100


# ---------------------------------------------------------------------------
# PIT cutoff


def _pit_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "period_end": "2024-06-30",
                "published_at": "2024-07-10 18:29:00",
                "available_at": "2024-07-10 18:29:00",
                "fetched_at": "2024-07-10 19:00:00",
                "source": "test",
                "content_hash": "a",
            },
            {
                "period_end": "2024-06-30",
                "published_at": "2024-07-10 18:31:00",
                "available_at": "2024-07-10 18:31:00",
                "fetched_at": "2024-07-10 19:00:00",
                "source": "test",
                "content_hash": "b",
            },
            {
                "period_end": "2024-06-30",
                "published_at": "2024-07-11 09:00:00",
                "available_at": "2024-07-11 09:00:00",
                "fetched_at": "2024-07-11 09:30:00",
                "source": "test",
                "content_hash": "c",
            },
        ]
    )


def test_pit_18_29_visible_18_31_invisible_on_signal_date() -> None:
    frame = _pit_frame()
    visible = asof_for_signal_date(frame, "2024-07-10")
    assert visible["content_hash"].tolist() == ["a"]


def test_pit_next_day_visible() -> None:
    frame = _pit_frame()
    visible = asof_for_signal_date(frame, "2024-07-11")
    # a (18:29 on 07-10) and b (18:31 on 07-10, <= 07-11 18:30) visible;
    # c (09:00 on 07-11) is visible too (<= 07-11 18:30).
    assert visible["content_hash"].tolist() == ["a", "b", "c"]


def test_pit_date_only_deferred_to_next_day() -> None:
    frame = pd.DataFrame(
        [
            {
                "period_end": "2024-06-30",
                "published_at": "2024-07-10",
                "available_at": "2024-07-10",
                "fetched_at": "2024-07-10 19:00:00",
                "source": "test",
                "content_hash": "date-only",
            }
        ]
    )
    # Same date: not visible (treated as released at 18:30).
    assert asof_for_signal_date(frame, "2024-07-10").empty
    # Later date: visible.
    visible = asof_for_signal_date(frame, "2024-07-11")
    assert visible["content_hash"].tolist() == ["date-only"]
    # Weekend: Friday release usable on Monday.
    frame.loc[0, "available_at"] = "2024-07-12"  # Friday
    frame.loc[0, "published_at"] = "2024-07-12"
    assert asof_for_signal_date(frame, "2024-07-12").empty
    assert not asof_for_signal_date(frame, "2024-07-15").empty  # Monday


def test_pit_strict_rejects_date_only() -> None:
    frame = pd.DataFrame(
        [
            {
                "period_end": "2024-06-30",
                "published_at": "2024-07-10",
                "available_at": "2024-07-10",
                "fetched_at": "2024-07-10 19:00:00",
                "source": "test",
                "content_hash": "date-only",
            }
        ]
    )
    with pytest.raises(ValueError, match="strict PIT"):
        asof_for_signal_date(frame, "2024-07-11", strict=True)


def test_pit_available_before_published_fails_audit() -> None:
    frame = _pit_frame()
    frame.loc[0, "available_at"] = "2024-07-10 18:00:00"
    with pytest.raises(ValueError, match="available_at earlier than published_at"):
        asof_for_signal_date(frame, "2024-07-10")


def test_pit_future_record_not_visible_to_earlier_signal() -> None:
    frame = _pit_frame()
    # A record available 2025-01-01 must never appear for a 2024 signal.
    frame.loc[2, "available_at"] = "2025-01-01 09:00:00"
    frame.loc[2, "published_at"] = "2025-01-01 09:00:00"
    visible = asof_for_signal_date(frame, "2024-12-31")
    assert "c" not in visible["content_hash"].tolist()
