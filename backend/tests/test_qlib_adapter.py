from datetime import date

import pandas as pd
import pytest
from app.adapters.qlib.dataset_exporter import QlibDatasetExporter, QlibTimeSplit
from app.adapters.qlib.prediction_importer import import_predictions


def test_qlib_export_has_strict_time_splits_and_available_at_filter(tmp_path) -> None:
    frame = pd.DataFrame(
        [
            {
                "date": "2023-12-29",
                "symbol": "A",
                "feature": 1.0,
                "label": 0.1,
                "available_at": "2023-12-29 10:00:00",
            },
            {
                "date": "2024-06-03",
                "symbol": "A",
                "feature": 2.0,
                "label": 0.2,
                "available_at": "2024-06-03 10:00:00",
            },
            {
                "date": "2025-06-02",
                "symbol": "A",
                "feature": 3.0,
                "label": 0.3,
                "available_at": "2025-06-02 10:00:00",
            },
            {
                "date": "2023-06-02",
                "symbol": "B",
                "feature": 99.0,
                "label": 9.9,
                "available_at": "2024-01-01 10:00:00",
            },
            {
                "date": "2024-06-03",
                "symbol": "C",
                "feature": 88.0,
                "label": 8.8,
                "available_at": "2024-06-03 20:00:00",
            },
        ]
    )
    split = QlibTimeSplit(
        train_start=date(2023, 1, 1),
        train_end=date(2023, 12, 31),
        validation_start=date(2024, 1, 1),
        validation_end=date(2024, 12, 31),
        test_start=date(2025, 1, 1),
        test_end=date(2025, 12, 31),
    )

    result = QlibDatasetExporter().export(
        frame, tmp_path / "dataset.parquet", split=split, feature_columns=["feature"]
    )
    exported = pd.read_parquet(result.path)

    assert exported["split"].tolist() == ["train", "validation", "test"]
    assert "B" not in exported["symbol"].tolist()
    assert "C" not in exported["symbol"].tolist()
    assert result.metadata["future_rows_rejected"] == 2


def test_qlib_split_overlap_is_rejected() -> None:
    with pytest.raises(ValueError, match="strictly ordered"):
        QlibTimeSplit(
            train_start=date(2023, 1, 1),
            train_end=date(2024, 1, 31),
            validation_start=date(2024, 1, 1),
            validation_end=date(2024, 12, 31),
            test_start=date(2025, 1, 1),
            test_end=date(2025, 12, 31),
        )


def test_qlib_predictions_remain_experiment_only() -> None:
    imported = import_predictions(
        pd.DataFrame([{"date": "2025-01-02", "symbol": "A", "prediction_score": 0.9}]),
        experiment_code="linear_rank_v1",
    )

    assert imported.metadata["experiment_only"] is True
    assert imported.metadata["production_enabled"] is False
