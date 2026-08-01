from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True, slots=True)
class AlphalensInput:
    factor: pd.Series
    prices: pd.DataFrame
    groups: pd.Series | None
    source_rows: int
    is_discrete: bool
