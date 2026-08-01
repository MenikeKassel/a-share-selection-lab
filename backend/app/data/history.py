from __future__ import annotations

import pandas as pd


def join_historical_state(
    daily: pd.DataFrame,
    state_history: pd.DataFrame,
    *,
    effective_column: str = "effective_date",
) -> pd.DataFrame:
    """Point-in-time join for industry, ST, universe, and other effective states."""
    required = {"date", "symbol"}
    if missing := required.difference(daily.columns):
        raise ValueError(f"daily data is missing columns: {sorted(missing)}")
    state_required = {"symbol", effective_column}
    if missing := state_required.difference(state_history.columns):
        raise ValueError(f"state history is missing columns: {sorted(missing)}")
    left = daily.copy()
    left["date"] = pd.to_datetime(left["date"]).dt.normalize()
    left["symbol"] = left["symbol"].astype(str)
    right = state_history.copy()
    right[effective_column] = pd.to_datetime(right[effective_column]).dt.normalize()
    right["symbol"] = right["symbol"].astype(str)
    state_columns = [
        column for column in right.columns if column not in {"symbol", effective_column}
    ]
    output: list[pd.DataFrame] = []
    for symbol, left_symbol in left.groupby("symbol", sort=False):
        right_symbol = right.loc[right["symbol"] == symbol].sort_values(effective_column)
        if right_symbol.empty:
            output.append(left_symbol)
            continue
        overlapping = [column for column in state_columns if column in left_symbol]
        left_symbol = left_symbol.drop(columns=overlapping)
        output.append(
            pd.merge_asof(
                left_symbol.sort_values("date"),
                right_symbol.drop(columns=["symbol"]),
                left_on="date",
                right_on=effective_column,
                direction="backward",
                allow_exact_matches=True,
            ).drop(columns=[effective_column])
        )
    return (
        pd.concat(output, ignore_index=True).sort_values(["date", "symbol"]).reset_index(drop=True)
    )
