import numpy as np
import pandas as pd
from app.research.factors.pipeline import FactorDefinition, FactorPipeline, PipelineConfig


def test_missing_factor_is_reweighted_instead_of_treated_as_zero() -> None:
    raw = pd.DataFrame(
        [
            {"date": "2026-01-02", "symbol": "A", "trend": 10.0, "quality": 10.0},
            {"date": "2026-01-02", "symbol": "B", "trend": 5.0, "quality": np.nan},
            {"date": "2026-01-02", "symbol": "C", "trend": 0.0, "quality": 0.0},
        ]
    )
    pipeline = FactorPipeline(
        definitions=[
            FactorDefinition("trend", "trend", weight=0.5, direction=1),
            FactorDefinition("quality", "fundamental", weight=0.5, direction=1),
        ],
        config=PipelineConfig(normalization="percentile", winsorize_limits=(0.0, 1.0)),
    )

    output = pipeline.transform(raw)
    scores = output.scores.set_index("symbol")
    details = output.details.set_index(["symbol", "factor_code"])

    assert scores.loc["B", "available_weight"] == 0.5
    assert scores.loc["B", "composite_score"] == details.loc[("B", "trend"), "percentile"] * 100
    assert bool(details.loc[("B", "quality"), "is_missing"]) is True
