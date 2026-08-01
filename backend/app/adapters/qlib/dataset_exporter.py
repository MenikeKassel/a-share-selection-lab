from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True, slots=True)
class QlibTimeSplit:
    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    test_start: date
    test_end: date

    def __post_init__(self) -> None:
        if not (
            self.train_start
            <= self.train_end
            < self.validation_start
            <= self.validation_end
            < self.test_start
            <= self.test_end
        ):
            raise ValueError(
                "train, validation, and test ranges must be strictly ordered and non-overlapping"
            )


@dataclass(frozen=True, slots=True)
class QlibDatasetExportResult:
    path: Path
    row_count: int
    feature_columns: list[str]
    metadata: dict[str, Any]


class QlibDatasetExporter:
    """Exports system Parquet features without handing raw storage to Qlib."""

    def export(
        self,
        frame: pd.DataFrame,
        output_path: Path,
        *,
        split: QlibTimeSplit,
        feature_columns: list[str],
        label_column: str = "label",
        information_cutoff: time = time(18, 30),
    ) -> QlibDatasetExportResult:
        required = {"date", "symbol", label_column, *feature_columns}
        if missing := required.difference(frame.columns):
            raise ValueError(f"Qlib export is missing columns: {sorted(missing)}")
        data = frame.copy()
        data["date"] = pd.to_datetime(data["date"]).dt.normalize()
        future_rows_rejected = 0
        if "available_at" in data.columns:
            available = pd.to_datetime(
                data["available_at"],
                errors="coerce",
                format="mixed",
            )
            cutoff = data["date"] + pd.Timedelta(
                hours=information_cutoff.hour,
                minutes=information_cutoff.minute,
                seconds=information_cutoff.second,
            )
            allowed = available.notna() & (available <= cutoff)
            future_rows_rejected = int((~allowed).sum())
            data = data.loc[allowed].copy()
        data["split"] = data["date"].map(lambda value: self._split_name(value, split))
        data = data.loc[data["split"].notna()].copy()
        data = data[["date", "symbol", *feature_columns, label_column, "split"]].sort_values(
            ["date", "symbol"]
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        data.to_parquet(output_path, index=False)
        return QlibDatasetExportResult(
            path=output_path,
            row_count=len(data),
            feature_columns=feature_columns,
            metadata={
                "future_rows_rejected": future_rows_rejected,
                "time_split": {
                    "train": [split.train_start.isoformat(), split.train_end.isoformat()],
                    "validation": [
                        split.validation_start.isoformat(),
                        split.validation_end.isoformat(),
                    ],
                    "test": [split.test_start.isoformat(), split.test_end.isoformat()],
                },
                "experiment_only": True,
                "production_enabled": False,
            },
        )

    @staticmethod
    def _split_name(value: pd.Timestamp, split: QlibTimeSplit) -> str | None:
        current = value.date()
        if split.train_start <= current <= split.train_end:
            return "train"
        if split.validation_start <= current <= split.validation_end:
            return "validation"
        if split.test_start <= current <= split.test_end:
            return "test"
        return None
