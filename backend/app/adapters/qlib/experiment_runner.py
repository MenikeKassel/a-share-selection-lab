from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.adapters import OptionalEngineUnavailableError


@dataclass(frozen=True, slots=True)
class QlibExperimentResult:
    predictions: pd.DataFrame
    metrics: dict[str, float]
    metadata: dict[str, Any]


class QlibExperimentRunner:
    """Minimal LightGBM ranking experiment tracked as a Qlib research run."""

    @staticmethod
    def is_available() -> bool:
        return find_spec("qlib") is not None and find_spec("lightgbm") is not None

    def run(
        self,
        dataset_path: Path,
        *,
        feature_columns: list[str],
        label_column: str = "label",
        model_config: dict[str, Any] | None = None,
    ) -> QlibExperimentResult:
        if not self.is_available():
            raise OptionalEngineUnavailableError(
                "Qlib/LightGBM 未安装；运行 uv sync --extra ml-research。"
            )
        # Third-party imports are intentionally confined to the Qlib adapter.
        import lightgbm as lgb
        import qlib

        data = pd.read_parquet(dataset_path)
        train = data.loc[data["split"] == "train"]
        validation = data.loc[data["split"] == "validation"]
        test = data.loc[data["split"] == "test"].copy()
        if train.empty or validation.empty or test.empty:
            raise ValueError("Qlib experiment requires non-empty train/validation/test splits")
        parameters = {
            "n_estimators": 100,
            "learning_rate": 0.05,
            "num_leaves": 31,
            "random_state": 42,
            "verbosity": -1,
            **(model_config or {}),
        }
        model = lgb.LGBMRegressor(**parameters)
        model.fit(
            train[feature_columns],
            train[label_column],
            eval_X=validation[feature_columns],
            eval_y=validation[label_column],
            callbacks=[lgb.early_stopping(20, verbose=False)],
        )
        test["prediction_score"] = model.predict(test[feature_columns])
        rank_ic_by_date = pd.Series(
            [
                section["prediction_score"].corr(section[label_column], method="spearman")
                for _, section in test.groupby("date", observed=True)
            ],
            dtype=float,
        )
        top_returns = (
            test.sort_values(["date", "prediction_score"], ascending=[True, False])
            .groupby("date")
            .head(20)[label_column]
            .mean()
        )
        metrics = {
            "rank_ic": self._finite(float(rank_ic_by_date.mean())),
            "top_n_return": self._finite(float(top_returns)),
        }
        return QlibExperimentResult(
            predictions=test[["date", "symbol", "prediction_score", label_column]],
            metrics=metrics,
            metadata={
                "engine": "qlib",
                "qlib_version": getattr(qlib, "__version__", "unknown"),
                "model_type": "LightGBM",
                "experiment_only": True,
                "production_enabled": False,
                "model_config": parameters,
            },
        )

    @staticmethod
    def _finite(value: float) -> float:
        return value if np.isfinite(value) else 0.0
