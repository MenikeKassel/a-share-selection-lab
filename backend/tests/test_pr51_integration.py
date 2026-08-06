"""PR 5.1 integration tests: security-master wiring and PIT as-of joins."""
import pandas as pd
from app.data.security_master import (
    SecurityMasterStatus,
    listing_days_for,
    normalise_security_master,
)
from app.research.factors.calculator import asof_join_available_data


def _real_master() -> pd.DataFrame:
    master, _ = normalise_security_master(
        pd.DataFrame(
            [
                {
                    "symbol": "000001.SZ",
                    "list_date": "1991-04-03",
                    "delist_date": pd.NaT,
                    "name": "平安银行",
                    "market": "主板",
                },
                {
                    "symbol": "600000.SH",
                    "list_date": "1999-11-10",
                    "delist_date": pd.NaT,
                    "name": "浦发银行",
                    "market": "主板",
                },
            ]
        )
    )
    return master


def _fallback_master() -> pd.DataFrame:
    master, _ = normalise_security_master(
        pd.DataFrame(
            [
                {
                    "symbol": "000001.SZ",
                    "list_date": "2018-01-02",
                    "last_observed_date": "2025-12-31",
                },
                {
                    "symbol": "600000.SH",
                    "list_date": "2018-01-02",
                    "last_observed_date": "2025-12-31",
                },
            ]
        )
    )
    return master


def test_listing_days_for_propagates_real_status() -> None:
    master = _real_master()
    market = pd.DataFrame(
        [
            {"date": "2024-01-02", "symbol": "000001.SZ"},
            {"date": "2024-01-02", "symbol": "600000.SH"},
        ]
    )
    listing_days, status = listing_days_for(market, master)

    assert status.real_listing_dates is True
    assert status.listing_date_source == "purchased_security_master"
    assert listing_days.iloc[0] > 11_000  # 1991 listing
    assert listing_days.iloc[1] > 8_000  # 1999 listing


def test_listing_days_for_propagates_fallback_status() -> None:
    """PR 5.1: a fallback master must NOT be reported as real."""
    master = _fallback_master()
    market = pd.DataFrame([{"date": "2024-01-02", "symbol": "000001.SZ"}])
    listing_days, status = listing_days_for(market, master)

    assert status.real_listing_dates is False
    assert status.listing_date_source == "daily_first_observed"
    # 2018-01-02 -> 2024-01-02 is ~2192 days; a real 1991 listing would be
    # ~11959 days.  Values alone cannot distinguish, but the status must.
    assert listing_days.iloc[0] > 2_000


def test_listing_days_for_missing_list_date_is_nan() -> None:
    master = _real_master()
    market = pd.DataFrame(
        [
            {"date": "2024-01-02", "symbol": "000001.SZ"},
            {"date": "2024-01-02", "symbol": "UNKNOWN.SZ"},
        ]
    )
    listing_days, _ = listing_days_for(market, master)
    assert pd.isna(listing_days.iloc[1])


def test_listing_days_for_explicit_status_override() -> None:
    master = _fallback_master()
    market = pd.DataFrame([{"date": "2024-01-02", "symbol": "000001.SZ"}])
    explicit = SecurityMasterStatus(False, "vendor_provided", 1)
    _, status = listing_days_for(market, master, explicit)
    assert status is explicit


def _pit_dataframe(date_only: bool = False) -> pd.DataFrame:
    available = "2024-07-10" if date_only else "2024-07-10 18:29:00"
    return pd.DataFrame(
        [
            {
                "symbol": "000001.SZ",
                "period_end": "2024-06-30",
                "published_at": available,
                "available_at": available,
                "fetched_at": "2024-07-10 19:00:00",
                "source": "test",
                "content_hash": "x",
                "roe": 0.10,
            }
        ]
    )


def _daily_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"date": "2024-07-10", "symbol": "000001.SZ", "close": 10.0},
            {"date": "2024-07-11", "symbol": "000001.SZ", "close": 10.1},
        ]
    )


def test_asof_join_keeps_18_29_record_on_same_day() -> None:
    joined = asof_join_available_data(_daily_frame(), _pit_dataframe())
    day_10 = joined.loc[joined["date"] == "2024-07-10"]
    assert len(day_10) == 1
    assert day_10.iloc[0]["roe"] == 0.10


def test_asof_join_blocks_18_31_record_on_same_day() -> None:
    frame = _pit_dataframe()
    frame.loc[0, "available_at"] = "2024-07-10 18:31:00"
    frame.loc[0, "published_at"] = "2024-07-10 18:31:00"
    joined = asof_join_available_data(_daily_frame(), frame)
    day_10 = joined.loc[joined["date"] == "2024-07-10"]
    day_11 = joined.loc[joined["date"] == "2024-07-11"]
    # Not known at the 07-10 cutoff -> NaN on 07-10, visible on 07-11.
    assert pd.isna(day_10.iloc[0]["roe"])
    assert day_11.iloc[0]["roe"] == 0.10


def test_asof_join_date_only_record_not_visible_same_day() -> None:
    """PR 5.1: date-only records must not leak into the same-day signal."""
    frame = _pit_dataframe(date_only=True)
    joined = asof_join_available_data(_daily_frame(), frame)
    day_10 = joined.loc[joined["date"] == "2024-07-10"]
    day_11 = joined.loc[joined["date"] == "2024-07-11"]
    assert pd.isna(day_10.iloc[0]["roe"])
    assert day_11.iloc[0]["roe"] == 0.10
