from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, cast

import pandas as pd
from app.adapters.market_data.tinyshare.client import TinyShareProviderError
from app.adapters.market_data.tinyshare.supplement import (
    TinyShareSnapshotCompleter,
    _next_trading_dates,
    _normalise_industry,
    _normalise_state,
    _normalise_universe,
)


def test_normalise_state_keeps_historical_industry_and_closes_st_status() -> None:
    calendar = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
            "is_open": [True, True, True],
        }
    )
    result = _normalise_state(
        [{"ts_code": "000001.SZ", "list_date": "20200101", "delist_date": ""}],
        [
            {
                "ts_code": "000001.SZ",
                "in_date": "20200101",
                "out_date": "20240103",
                "l2_name": "old",
            },
            {
                "ts_code": "000001.SZ",
                "in_date": "20240104",
                "out_date": "",
                "l2_name": "new",
            },
        ],
        [{"ts_code": "000001.SZ", "trade_date": "20240102"}],
        calendar=calendar,
    )

    old = result.loc[result["effective_date"] == pd.Timestamp(date(2020, 1, 1))].iloc[0]
    st = result.loc[result["effective_date"] == pd.Timestamp(date(2024, 1, 2))].iloc[0]
    after_st = result.loc[result["effective_date"] == pd.Timestamp(date(2024, 1, 3))].iloc[0]
    new = result.loc[result["effective_date"] == pd.Timestamp(date(2024, 1, 4))].iloc[0]

    assert old["industry"] == "old"
    assert bool(st["is_st"]) is True
    assert bool(after_st["is_st"]) is False
    assert new["industry"] == "new"
    assert pd.Timestamp(old["list_date"]) == pd.Timestamp("2020-01-01")


def test_financial_availability_uses_next_open_trading_date() -> None:
    values = pd.Series(pd.to_datetime(["2024-01-05", "2024-01-08"]))
    calendar = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-05", "2024-01-08", "2024-01-09"]),
            "is_open": [True, True, True],
        }
    )

    output = _next_trading_dates(values, calendar)

    assert output.tolist() == [pd.Timestamp("2024-01-08"), pd.Timestamp("2024-01-09")]


def test_industry_normaliser_accepts_index_membership_con_code() -> None:
    result = _normalise_industry(
        [
            {
                "index_code": "801010.SI",
                "con_code": "000001.SZ",
                "in_date": "20200101",
                "index_name": "banking",
            }
        ]
    )

    assert result.iloc[0]["symbol"] == "000001.SZ"
    assert result.iloc[0]["industry"] == "banking"


def test_universe_prefers_suffixed_ts_code_without_duplicate_columns() -> None:
    result = _normalise_universe(
        [
            {
                "ts_code": "000001.SZ",
                "symbol": "000001",
                "list_date": "19910403",
                "name": "fixture",
                "market": "main",
            }
        ]
    )

    assert result.columns.is_unique
    assert result.iloc[0]["symbol"] == "000001.SZ"


def test_valuation_export_batches_dates_and_writes_point_in_time_parquet(
    tmp_path: Path,
) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[list[dict[str, Any]]] = []

        def call_many(
            self, method: str, params: list[dict[str, Any]]
        ) -> list[list[dict[str, Any]]]:
            assert method == "daily_basic"
            self.calls.append(params)
            return [
                [
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": item["trade_date"],
                        "pe_ttm": 10.0,
                        "pb": 1.2,
                        "ps_ttm": 2.0,
                        "dv_ttm": 1.5,
                        "total_mv": 100_000.0,
                    }
                ]
                for item in params
            ]

    calendar = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "is_open": [True, True],
        }
    )
    client = FakeClient()
    completer = TinyShareSnapshotCompleter(cast(Any, client))

    path = completer._write_valuations(
        tmp_path / "valuations.parquet",
        calendar,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        batch_size=1,
    )

    output = pd.read_parquet(path)
    assert len(client.calls) == 2
    assert output["date"].tolist() == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")]
    assert output["available_at"].str.endswith("T18:30:00+08:00").all()
    assert output["market_cap"].tolist() == [100_000.0, 100_000.0]


def test_resilient_batch_splits_after_worker_limit() -> None:
    class SizeLimitedClient:
        def call_many(
            self, method: str, params: list[dict[str, Any]]
        ) -> list[list[dict[str, Any]]]:
            assert method == "daily_basic"
            if len(params) > 2:
                raise TinyShareProviderError("worker exited without a message")
            return [[{"value": item["value"]}] for item in params]

    completer = TinyShareSnapshotCompleter(cast(Any, SizeLimitedClient()))

    batches = completer._call_many_resilient(
        "daily_basic", [{"value": value} for value in range(5)]
    )

    assert [rows[0]["value"] for rows in batches] == list(range(5))


def test_resilient_single_call_retries_worker_exit() -> None:
    class FlakyClient:
        def __init__(self) -> None:
            self.attempts = 0

        def call(self, method: str, params: dict[str, Any]) -> list[dict[str, Any]]:
            self.attempts += 1
            if self.attempts < 3:
                raise TinyShareProviderError("worker exited without a message")
            return [{"method": method, **params}]

    client = FlakyClient()
    completer = TinyShareSnapshotCompleter(cast(Any, client))

    rows = completer._call_resilient("income_vip", {"period": "20240331"})

    assert client.attempts == 3
    assert rows == [{"method": "income_vip", "period": "20240331"}]
