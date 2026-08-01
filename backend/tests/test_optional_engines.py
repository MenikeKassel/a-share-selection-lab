from importlib import metadata

import pandas as pd
import pytest
from app.adapters import OptionalEngineUnavailableError
from app.adapters.alphalens.adapter import AlphalensFactorAnalysisAdapter
from app.adapters.qlib.experiment_runner import QlibExperimentRunner
from app.adapters.vectorbt.adapter import VectorBTResearchAdapter
from app.adapters.vectorbt.schemas import VectorBTParameterSet
from app.domain.protocols import FactorAnalysisRequest
from app.engines.registry import EngineRegistry


def test_optional_engine_registry_does_not_import_or_require_engines() -> None:
    statuses = {item.engine_type: item for item in EngineRegistry().statuses()}

    assert set(statuses) == {"alphalens", "vectorbt", "rqalpha", "qlib"}
    assert statuses["alphalens"].required is False
    assert statuses["vectorbt"].formal_result is False
    assert statuses["qlib"].production_enabled is False
    assert "license" in statuses["vectorbt"].license_notice.lower()


def test_registry_marks_all_optional_engines_unavailable_when_not_installed(
    monkeypatch,
) -> None:
    def missing_distribution(_name: str) -> str:
        raise metadata.PackageNotFoundError

    monkeypatch.setattr("app.engines.registry.metadata.version", missing_distribution)
    monkeypatch.setattr("app.engines.registry.find_spec", lambda _name: None)

    statuses = EngineRegistry().statuses()

    assert all(not item.installed and not item.available for item in statuses)
    assert all("uv sync --extra" in (item.unavailable_reason or "") for item in statuses)


def test_optional_adapters_raise_clear_unavailable_errors(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(AlphalensFactorAnalysisAdapter, "is_available", staticmethod(lambda: False))
    with pytest.raises(OptionalEngineUnavailableError, match="factor-research"):
        AlphalensFactorAnalysisAdapter().analyze_frames(
            FactorAnalysisRequest(
                factor_code="factor",
                start_date=pd.Timestamp("2026-01-02").date(),
                end_date=pd.Timestamp("2026-01-05").date(),
                horizons=[1],
            ),
            pd.DataFrame([{"date": "2026-01-02", "symbol": "A", "factor": 1.0}]),
            pd.DataFrame([{"date": "2026-01-02", "symbol": "A", "close": 10.0}]),
        )

    monkeypatch.setattr(VectorBTResearchAdapter, "is_available", staticmethod(lambda: False))
    with pytest.raises(OptionalEngineUnavailableError, match="fast-backtest"):
        VectorBTResearchAdapter().run(
            pd.DataFrame(),
            pd.DataFrame(),
            VectorBTParameterSet(
                top_n=1,
                holding_period=1,
                rebalance_frequency="daily",
                commission_rate=0.0,
                slippage_bps=0.0,
            ),
        )

    monkeypatch.setattr(QlibExperimentRunner, "is_available", staticmethod(lambda: False))
    with pytest.raises(OptionalEngineUnavailableError, match="ml-research"):
        QlibExperimentRunner().run(
            tmp_path / "missing.parquet",
            feature_columns=["feature"],
        )
