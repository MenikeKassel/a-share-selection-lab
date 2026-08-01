from __future__ import annotations

import pandas as pd

from app.adapters.alphalens.schemas import AlphalensInput

_FORBIDDEN_FACTOR_TOKENS = ("forward_return", "future_return", "future_", "label_")


def prepare_alphalens_input(
    factor_frame: pd.DataFrame,
    price_frame: pd.DataFrame,
    factor_code: str,
) -> AlphalensInput:
    """Convert system long tables without letting forward returns leak into factors."""
    suspicious = [
        column
        for column in factor_frame.columns
        if any(token in column.lower() for token in _FORBIDDEN_FACTOR_TOKENS)
    ]
    if suspicious:
        raise ValueError(
            f"factor input contains future/forward-looking columns: {sorted(suspicious)}"
        )
    factor_required = {"date", "symbol", factor_code}
    price_required = {"date", "symbol", "close"}
    missing_factor = factor_required.difference(factor_frame.columns)
    missing_price = price_required.difference(price_frame.columns)
    if missing_factor:
        raise ValueError(f"factor frame is missing columns: {sorted(missing_factor)}")
    if missing_price:
        raise ValueError(f"price frame is missing columns: {sorted(missing_price)}")

    factors = factor_frame.copy()
    factors["date"] = pd.to_datetime(factors["date"]).dt.tz_localize(None).dt.normalize()
    factors["symbol"] = factors["symbol"].astype(str)
    if factors.duplicated(["date", "symbol"]).any():
        raise ValueError("factor frame contains duplicate date/symbol rows")
    raw_factor = factors[factor_code]
    is_discrete = bool(pd.api.types.is_bool_dtype(raw_factor) or raw_factor.dropna().nunique() <= 2)
    values = (
        raw_factor.astype("boolean").astype(float)
        if pd.api.types.is_bool_dtype(raw_factor)
        else pd.to_numeric(raw_factor, errors="coerce")
    )
    factor = pd.Series(
        values.to_numpy(),
        index=pd.MultiIndex.from_frame(factors[["date", "symbol"]], names=["date", "asset"]),
        name="factor",
        dtype=float,
    ).sort_index()

    prices = price_frame.copy()
    prices["date"] = pd.to_datetime(prices["date"]).dt.tz_localize(None).dt.normalize()
    prices["symbol"] = prices["symbol"].astype(str)
    if prices.duplicated(["date", "symbol"]).any():
        raise ValueError("price frame contains duplicate date/symbol rows")
    price_matrix = (
        prices.pivot(index="date", columns="symbol", values="close").sort_index().astype(float)
    )
    price_matrix.index.name = "date"
    price_matrix.columns.name = "asset"

    groups: pd.Series | None = None
    if "industry" in factors.columns:
        groups = pd.Series(
            factors["industry"].fillna("unknown").astype(str).to_numpy(),
            index=factor.index,
            name="group",
        ).sort_index()
    return AlphalensInput(
        factor=factor,
        prices=price_matrix,
        groups=groups,
        source_rows=int(factor.notna().sum()),
        is_discrete=is_discrete,
    )
