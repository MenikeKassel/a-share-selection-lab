from datetime import date
from importlib.util import find_spec

import numpy as np
import pandas as pd
import pytest
from app.adapters.alphalens.adapter import AlphalensFactorAnalysisAdapter
from app.adapters.qlib.dataset_exporter import QlibDatasetExporter, QlibTimeSplit
from app.adapters.qlib.experiment_runner import QlibExperimentRunner
from app.adapters.rqalpha.adapter import RQAlphaValidationAdapter
from app.adapters.vectorbt.adapter import VectorBTResearchAdapter
from app.adapters.vectorbt.schemas import VectorBTParameterSet
from app.domain.protocols import FactorAnalysisRequest
from app.research.factor_analysis import NativeFactorAnalysisEngine


@pytest.mark.optional
@pytest.mark.skipif(find_spec("alphalens") is None, reason="Alphalens is optional")
def test_native_and_alphalens_rank_ic_are_consistent() -> None:
    dates = pd.bdate_range("2025-01-02", periods=45)
    symbols = [f"S{index}" for index in range(6)]
    factor_rows = []
    price_rows = []
    for day_index, trade_date in enumerate(dates):
        for symbol_index, symbol in enumerate(symbols):
            factor_rows.append(
                {
                    "date": trade_date,
                    "symbol": symbol,
                    "quality_factor": float(symbol_index),
                    "industry": "G1" if symbol_index < 3 else "G2",
                }
            )
            price_rows.append(
                {
                    "date": trade_date,
                    "symbol": symbol,
                    "close": 10.0 * (1.0 + 0.0005 * (symbol_index + 1)) ** day_index,
                }
            )
    factors = pd.DataFrame(factor_rows)
    prices = pd.DataFrame(price_rows)
    request = FactorAnalysisRequest(
        factor_code="quality_factor",
        start_date=date(2025, 1, 2),
        end_date=dates[-1].date(),
        horizons=[1, 5],
        group_count=3,
    )

    native = NativeFactorAnalysisEngine().analyze_frames(request, factors, prices)
    external = AlphalensFactorAnalysisAdapter().analyze_frames(request, factors, prices)

    assert external.metadata["analysis_engine"] == "alphalens"
    assert external.coverage > 0.7
    assert external.rank_ic[1] == pytest.approx(native.rank_ic[1], abs=0.05)


@pytest.mark.optional
@pytest.mark.skipif(find_spec("vectorbt") is None, reason="VectorBT is optional")
def test_vectorbt_adapter_runs_a_real_parameter_case() -> None:
    dates = pd.bdate_range("2026-01-02", periods=12)
    prices = pd.DataFrame(
        [
            {
                "date": trade_date,
                "symbol": symbol,
                "open": 10.0 + day_index * (0.2 if symbol == "A" else -0.1),
                "close": 10.0 + day_index * (0.2 if symbol == "A" else -0.1),
            }
            for day_index, trade_date in enumerate(dates)
            for symbol in ("A", "B")
        ]
    )
    scores = pd.DataFrame(
        [
            {
                "signal_date": trade_date,
                "symbol": symbol,
                "score": 1.0 if symbol == "A" else 0.0,
            }
            for trade_date in dates[:-1]
            for symbol in ("A", "B")
        ]
    )

    result = VectorBTResearchAdapter().run(
        prices,
        scores,
        VectorBTParameterSet(
            top_n=1,
            holding_period=3,
            rebalance_frequency="weekly",
            commission_rate=0.0,
            slippage_bps=0.0,
        ),
        initial_cash=100_000,
    )

    assert np.isfinite(result.cumulative_return)
    assert result.metadata["research_engine"] == "vectorbt"
    assert result.metadata["formal_ashare_validation"] == "required"


@pytest.mark.optional
@pytest.mark.skipif(
    find_spec("qlib") is None or find_spec("lightgbm") is None,
    reason="Qlib and LightGBM are optional",
)
def test_qlib_adapter_runs_minimal_lightgbm_experiment(tmp_path) -> None:
    dates = pd.bdate_range("2023-01-02", periods=60)
    frame = pd.DataFrame(
        [
            {
                "date": trade_date,
                "symbol": f"S{symbol_index}",
                "feature_quality": float(symbol_index) + day_index * 0.01,
                "feature_momentum": float(symbol_index % 3) - day_index * 0.001,
                "label": symbol_index * 0.02 + day_index * 0.0001,
                "available_at": trade_date,
            }
            for day_index, trade_date in enumerate(dates)
            for symbol_index in range(8)
        ]
    )
    dataset = QlibDatasetExporter().export(
        frame,
        tmp_path / "qlib-dataset.parquet",
        split=QlibTimeSplit(
            train_start=dates[0].date(),
            train_end=dates[19].date(),
            validation_start=dates[20].date(),
            validation_end=dates[39].date(),
            test_start=dates[40].date(),
            test_end=dates[-1].date(),
        ),
        feature_columns=["feature_quality", "feature_momentum"],
    )

    result = QlibExperimentRunner().run(
        dataset.path,
        feature_columns=["feature_quality", "feature_momentum"],
        model_config={"n_estimators": 20, "num_leaves": 7},
    )

    assert result.metadata["engine"] == "qlib"
    assert result.metadata["experiment_only"] is True
    assert result.metadata["production_enabled"] is False
    assert np.isfinite(result.metrics["rank_ic"])


@pytest.mark.optional
@pytest.mark.skipif(find_spec("rqalpha") is None, reason="RQAlpha is optional")
def test_rqalpha_adapter_generates_an_importable_validation_strategy(tmp_path) -> None:
    signals = pd.DataFrame(
        [
            {
                "signal_date": "2026-01-02",
                "symbol": "600000.XSHG",
                "target_weight": 0.5,
            },
            {
                "signal_date": "2026-01-02",
                "symbol": "000001.XSHE",
                "target_weight": 0.5,
            },
        ]
    )

    artifacts = RQAlphaValidationAdapter().prepare_validation(signals, tmp_path)
    strategy_text = (tmp_path / "strategy.py").read_text(encoding="utf-8")

    assert RQAlphaValidationAdapter().status()["available"] is True
    assert artifacts["signal_path"].endswith("signals.csv")
    assert "order_target_percent" in strategy_text
    assert "signal_date" in strategy_text
