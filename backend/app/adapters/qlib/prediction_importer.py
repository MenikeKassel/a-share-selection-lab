from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True, slots=True)
class ImportedPredictions:
    predictions: pd.DataFrame
    metadata: dict[str, Any]


def import_predictions(frame: pd.DataFrame, *, experiment_code: str) -> ImportedPredictions:
    required = {"date", "symbol", "prediction_score"}
    if missing := required.difference(frame.columns):
        raise ValueError(f"Qlib predictions are missing columns: {sorted(missing)}")
    output = frame.copy()
    output["date"] = pd.to_datetime(output["date"]).dt.normalize()
    output["symbol"] = output["symbol"].astype(str)
    if output.duplicated(["date", "symbol"]).any():
        raise ValueError("Qlib predictions contain duplicate date/symbol rows")
    output["experiment_code"] = experiment_code
    return ImportedPredictions(
        predictions=output.sort_values(["date", "prediction_score"], ascending=[True, False]),
        metadata={
            "experiment_code": experiment_code,
            "experiment_only": True,
            "production_enabled": False,
        },
    )
