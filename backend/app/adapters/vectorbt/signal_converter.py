from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Any

import pandas as pd


@dataclass(frozen=True, slots=True)
class VectorBTSignalMatrix:
    """Research price matrix for VectorBT (PR 3).

    ``execution_open`` is the causal-adjusted next-open price used to fill
    entries/exits; it is NEVER forward-filled: a missing open means the
    symbol is not tradable that day (VectorBT must skip it, not fill from
    the previous day).  ``valuation_close`` may be forward-filled for mark-
    to-market only, and is kept separate from the execution price.
    """

    execution_open: pd.DataFrame
    valuation_close: pd.DataFrame
    tradable_mask: pd.DataFrame
    entries: pd.DataFrame
    exits: pd.DataFrame
    metadata: dict[str, Any]


def convert_scores_to_signals(
    scores: pd.DataFrame,
    prices: pd.DataFrame,
    *,
    top_n: int,
    holding_period: int,
    rebalance_frequency: str,
) -> VectorBTSignalMatrix:
    if top_n < 1 or holding_period < 1:
        raise ValueError("top_n and holding_period must be positive")
    if rebalance_frequency not in {"daily", "weekly"}:
        raise ValueError("rebalance_frequency must be daily or weekly")
    score_required = {"signal_date", "symbol", "score"}
    price_required = {"date", "symbol", "open", "close"}
    if missing := score_required.difference(scores.columns):
        raise ValueError(f"score frame is missing columns: {sorted(missing)}")
    if missing := price_required.difference(prices.columns):
        raise ValueError(f"price frame is missing columns: {sorted(missing)}")

    price_frame = prices.copy()
    price_frame["date"] = pd.to_datetime(price_frame["date"]).dt.normalize()
    price_frame["symbol"] = price_frame["symbol"].astype(str)
    # Execution price: next open.  NO ffill: a missing open is untradable.
    execution_open = (
        price_frame.pivot(index="date", columns="symbol", values="open")
        .sort_index()
        .astype(float)
    )
    # Valuation price: close, may be forward-filled for mark-to-market only.
    valuation_close = (
        price_frame.pivot(index="date", columns="symbol", values="close")
        .sort_index()
        .astype(float)
    )
    # Tradable mask: valid open and (volume not zero when available).
    tradable = execution_open.notna()
    if "volume" in price_frame:
        volume = (
            price_frame.pivot(index="date", columns="symbol", values="volume")
            .sort_index()
            .astype(float)
        )
        tradable &= volume.fillna(0) > 0
    if "suspended" in price_frame:
        suspended = (
            price_frame.pivot(index="date", columns="symbol", values="suspended")
            .sort_index()
            .fillna(False)
        )
        tradable &= ~suspended.astype(bool)
    entries = pd.DataFrame(False, index=execution_open.index, columns=execution_open.columns)
    exits = pd.DataFrame(False, index=execution_open.index, columns=execution_open.columns)

    signal_frame = scores.copy()
    signal_frame["signal_date"] = pd.to_datetime(signal_frame["signal_date"]).dt.normalize()
    signal_frame["symbol"] = signal_frame["symbol"].astype(str)
    dates = list(execution_open.index)
    next_date = {current: following for current, following in pairwise(dates)}
    selected_signal_dates = _rebalance_dates(
        sorted(signal_frame["signal_date"].unique()), rebalance_frequency
    )

    for signal_date in selected_signal_dates:
        execution_date = next_date.get(pd.Timestamp(signal_date))
        if execution_date is None:
            continue
        group = signal_frame.loc[signal_frame["signal_date"] == signal_date]
        selected = (
            group.loc[group["symbol"].isin(execution_open.columns)]
            .sort_values(["score", "symbol"], ascending=[False, True])
            .head(top_n)["symbol"]
            .tolist()
        )
        for symbol in selected:
            entries.loc[execution_date, symbol] = True
            exit_index = min(dates.index(execution_date) + holding_period, len(dates) - 1)
            if exit_index <= dates.index(execution_date):
                continue
            # PR 4.1: roll the exit forward to the next tradable day when
            # the scheduled day is untradable; never cancel silently.
            exit_date = dates[exit_index]
            while not bool(tradable.loc[exit_date, symbol]):
                exit_index += 1
                if exit_index >= len(dates):
                    break
                exit_date = dates[exit_index]
            if exit_index < len(dates) and exit_index > dates.index(execution_date):
                exits.loc[exit_date, symbol] = True
    return VectorBTSignalMatrix(
        execution_open=execution_open,
        valuation_close=valuation_close.ffill(),
        tradable_mask=tradable,
        entries=entries,
        exits=exits,
        metadata={
            "signal_lag_bars": 1,
            "signal_timing": "after_close",
            "execution_proxy": "next_bar_open",
            "execution_price_ffill": False,
            "valuation_price_ffill": True,
            "holding_period": holding_period,
            "rebalance_frequency": rebalance_frequency,
        },
    )


def _rebalance_dates(dates: list[pd.Timestamp], frequency: str) -> list[pd.Timestamp]:
    if frequency == "daily":
        return dates
    if not dates:
        return []
    frame = pd.DataFrame({"date": pd.to_datetime(dates)})
    frame["week"] = frame["date"].dt.to_period("W-FRI")
    return frame.groupby("week")["date"].max().tolist()
