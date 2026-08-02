from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from app.adapters.market_data.base import DailyBarsRequest, MinuteBarsRequest
from app.adapters.market_data.freestockdb.adapter import (
    FreeStockDBHttpAdapter,
    apply_adjustment_factors,
    normalize_daily_frame,
    normalize_minute_frame,
)


def test_daily_normalization_filters_non_stocks_and_rebuilds_pre_close() -> None:
    frame = normalize_daily_frame(
        pd.DataFrame(
            [
                {
                    "date": 20260730,
                    "code": "600001",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "pre_close": 10.2,
                    "volume": 100,
                    "amount": 1000,
                },
                {
                    "date": 20260731,
                    "code": "600001",
                    "open": 10.5,
                    "high": 12,
                    "low": 10,
                    "close": 11.5,
                    "pre_close": 999,
                    "volume": 200,
                    "amount": 2200,
                },
                {
                    "date": 20260731,
                    "code": "510300",
                    "open": 4,
                    "high": 4,
                    "low": 4,
                    "close": 4,
                    "volume": 100,
                    "amount": 400,
                },
            ]
        )
    )
    assert frame["symbol"].tolist() == ["600001", "600001"]
    assert frame["volume_unit"].unique().tolist() == ["shares"]
    assert frame.iloc[1]["pre_close"] == pytest.approx(10.5)
    assert frame.iloc[1]["source_pre_close"] == pytest.approx(999)


def test_minute_normalization_preserves_241_bar_session_and_shares() -> None:
    dates = [
        int(timestamp.strftime("%Y%m%d%H%M%S"))
        for timestamp in pd.date_range(
            "2026-07-31 09:30", periods=120, freq="min"
        ).append(
            pd.date_range("2026-07-31 13:00", periods=121, freq="min")
        )
    ]
    frame = normalize_minute_frame(
        pd.DataFrame(
            {
                "code": ["600001"] * 241,
                "date": dates,
                "open": [10.0] * 241,
                "close": [10.0] * 241,
                "high": [10.1] * 241,
                "low": [9.9] * 241,
                "volume": [100] * 241,
                "amount": [1000] * 241,
            }
        )
    )
    assert len(frame) == 241
    assert frame.iloc[0]["timestamp"].strftime("%H:%M:%S") == "09:30:00"
    assert frame.iloc[-1]["timestamp"].strftime("%H:%M:%S") == "15:00:00"
    assert frame["volume_unit"].unique().tolist() == ["shares"]


def test_adjustment_factors_create_qfq_and_hfq_views() -> None:
    daily = normalize_daily_frame(
        pd.DataFrame(
            [
                {
                    "date": 20260730,
                    "code": "600001",
                    "open": 10,
                    "high": 10,
                    "low": 10,
                    "close": 10,
                    "volume": 100,
                    "amount": 1000,
                },
                {
                    "date": 20260731,
                    "code": "600001",
                    "open": 20,
                    "high": 20,
                    "low": 20,
                    "close": 20,
                    "volume": 100,
                    "amount": 2000,
                },
            ]
        )
    )
    factors = pd.DataFrame(
        [
            {
                "symbol": "600001",
                "effective_date": date(2020, 1, 1),
                "div": 0,
                "give": 0,
                "trans": 0,
                "mult": 1,
                "cum": 1,
            },
            {
                "symbol": "600001",
                "effective_date": date(2026, 7, 31),
                "div": 0,
                "give": 0,
                "trans": 0,
                "mult": 2,
                "cum": 2,
            },
        ]
    )
    output = apply_adjustment_factors(daily, factors)
    assert output["adj_factor"].tolist() == [1, 2]
    assert output.iloc[0]["qfq_close"] == pytest.approx(5)
    assert output.iloc[1]["qfq_close"] == pytest.approx(20)
    assert output.iloc[0]["hfq_close"] == pytest.approx(10)


def test_provider_rejects_non_local_endpoint() -> None:
    with pytest.raises(ValueError, match="local"):
        FreeStockDBHttpAdapter(base_url="https://example.com")


def test_provider_contract_can_be_exercised_without_a_live_service() -> None:
    class FixtureAdapter(FreeStockDBHttpAdapter):
        def _request_json(self, params: dict[str, str]):  # type: ignore[no-untyped-def]
            if params["cmd"] == "vals" and params["t"] == "日k":
                return [
                    {
                        "date": 20260731,
                        "code": "600001",
                        "open": 10,
                        "high": 11,
                        "low": 9,
                        "close": 10.5,
                        "volume": 100,
                        "amount": 1000,
                    }
                ]
            if params["cmd"] == "vals" and params["t"] == "分钟k":
                return []
            return []

    adapter = FixtureAdapter()
    daily = adapter.fetch_daily(DailyBarsRequest(date(2026, 7, 31), date(2026, 7, 31), ("600001",)))
    minute = adapter.fetch_minute(
        MinuteBarsRequest("20260731000000", "20260731235959", ("600001",))
    )
    assert daily.iloc[0]["symbol"] == "600001"
    assert minute == {}


def test_provider_uses_prefix_batches_for_large_daily_and_factor_requests() -> None:
    class BatchFixtureAdapter(FreeStockDBHttpAdapter):
        def _request_json(self, params: dict[str, str]):  # type: ignore[no-untyped-def]
            if params == {
                "cmd": "vals",
                "t": "日k",
                "k1": "key:600*",
                "k2": "fwd:20260701,20260731",
            }:
                return [
                    {
                        "date": 20260731,
                        "code": "600001",
                        "open": 10,
                        "high": 11,
                        "low": 9,
                        "close": 10.5,
                        "volume": 100,
                        "amount": 1000,
                    }
                ]
            if params == {
                "cmd": "get",
                "t": "复权",
                "k1": "key:600*",
                "k2": "all:",
            }:
                return [[
                    "复权:600001:20260731",
                    {"div": 0, "give": 0, "trans": 0, "mult": 1, "cum": 1},
                ]]
            return []

    adapter = BatchFixtureAdapter()
    daily = adapter._fetch_prefix_daily(
        "600", DailyBarsRequest(date(2026, 7, 1), date(2026, 7, 31))
    )
    factors = adapter._fetch_factor_prefix("600")
    assert daily[0]["code"] == "600001"
    assert factors[0]["symbol"] == "600001"
    assert factors[0]["effective_date"] == date(2026, 7, 31)
