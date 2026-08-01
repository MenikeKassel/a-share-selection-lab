from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd


def filter_date_window(
    frame: pd.DataFrame,
    *,
    column: str,
    start: date,
    end: date,
) -> pd.DataFrame:
    if column not in frame:
        raise ValueError(f"date window column is missing: {column}")
    output = frame.copy()
    output[column] = pd.to_datetime(output[column]).dt.tz_localize(None).dt.normalize()
    mask = output[column].between(pd.Timestamp(start), pd.Timestamp(end))
    return output.loc[mask].copy()


def read_tabular(path_value: str | Path) -> pd.DataFrame:
    path = Path(path_value)
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix in {".csv", ".txt"}:
        return pd.read_csv(path)
    if suffix in {".json", ".jsonl"}:
        return pd.read_json(path, lines=suffix == ".jsonl")
    raise ValueError(f"unsupported tabular artifact: {suffix}")
