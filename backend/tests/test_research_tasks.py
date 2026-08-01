from datetime import date

import pandas as pd
from app.adapters import OptionalEngineUnavailableError
from app.api.schemas import FactorAnalysisRunRequest, RQAlphaValidationRequest
from app.core.config import Settings
from app.db.repositories import (
    BacktestRunRepository,
    ComparisonRepository,
    FactorResultRepository,
)
from app.db.session import create_session_factory, initialize_database
from app.services.research_tasks import ResearchTaskService


def test_native_factor_baseline_is_saved_when_alphalens_is_unavailable(
    tmp_path, monkeypatch
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'research.db').as_posix()}"
    initialize_database(database_url)
    factory = create_session_factory(database_url)
    data_root = tmp_path / "data"
    data_root.mkdir()
    factor_path = data_root / "factors.csv"
    price_path = data_root / "prices.csv"
    dates = pd.bdate_range("2026-01-02", periods=5)
    factors = []
    prices = []
    for day_number, trade_date in enumerate(dates):
        for symbol_number, symbol in enumerate(["A", "B", "C"]):
            factors.append(
                {
                    "date": trade_date,
                    "symbol": symbol,
                    "quality": float(symbol_number),
                    "industry": "test",
                }
            )
            prices.append(
                {
                    "date": trade_date,
                    "symbol": symbol,
                    "close": 10.0 + day_number * (symbol_number + 1),
                }
            )
    pd.DataFrame(factors).to_csv(factor_path, index=False)
    pd.DataFrame(prices).to_csv(price_path, index=False)

    def unavailable(*_args, **_kwargs):
        raise OptionalEngineUnavailableError("not installed")

    monkeypatch.setattr(
        "app.services.research_tasks.AlphalensFactorAnalysisAdapter.analyze",
        unavailable,
    )

    settings = Settings(
        _env_file=None,
        database_url=database_url,
        data_root=data_root,
        artifact_root=tmp_path / "artifacts",
    )
    with factory() as session:
        record = ResearchTaskService(session, settings).run_factor_analysis(
            FactorAnalysisRunRequest(
                factor_code="quality",
                start_date=date(2026, 1, 2),
                end_date=date(2026, 1, 8),
                horizons=[1],
                factor_path=str(factor_path),
                price_path=str(price_path),
            )
        )
        results = FactorResultRepository(session).list()

    assert record.status == "unavailable"
    assert [(item.analysis_engine, item.factor_code) for item in results] == [("native", "quality")]


def test_rqalpha_validation_persists_comparison_with_formal_engine(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{(tmp_path / 'comparison.db').as_posix()}"
    initialize_database(database_url)
    factory = create_session_factory(database_url)
    data_root = tmp_path / "data"
    data_root.mkdir()
    signals_path = data_root / "signals.csv"
    pd.DataFrame(
        [
            {
                "signal_date": "2026-01-02",
                "symbol": "000001.XSHE",
                "target_weight": 1.0,
            }
        ]
    ).to_csv(signals_path, index=False)

    def fake_run(*_args, **_kwargs):
        return {"result_path": str(tmp_path / "rqalpha.pkl")}

    monkeypatch.setattr(
        "app.services.research_tasks.RQAlphaValidationAdapter.run",
        fake_run,
    )
    monkeypatch.setattr(
        "app.services.research_tasks.import_rqalpha_result",
        lambda _path: {
            "performance": {"total_return": 0.08, "total_fees": 80.0},
            "trades": [{"symbol": "000001.XSHE"}],
        },
    )
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        data_root=data_root,
        artifact_root=tmp_path / "artifacts",
    )
    with factory() as session:
        formal = BacktestRunRepository(session).create(
            engine_type="ashare_daily_v1",
            strategy_code="trend_quality_v1",
            start_date=date(2026, 1, 2),
            end_date=date(2026, 1, 30),
            config={},
            formal_ashare_result=True,
        )
        BacktestRunRepository(session).finish(
            formal,
            status="succeeded",
            result={
                "performance": {
                    "tradable_return": 0.10,
                    "total_fees": 100.0,
                },
                "trades": [{"symbol": "000001.XSHE"}],
                "execution_failures": [],
            },
        )
        run = ResearchTaskService(session, settings).run_rqalpha(
            RQAlphaValidationRequest(
                strategy_code="trend_quality_v1",
                start_date=date(2026, 1, 2),
                end_date=date(2026, 1, 30),
                signal_path=str(signals_path),
                formal_backtest_run_id=formal.id,
            )
        )
        comparisons = ComparisonRepository(session).list()

    assert run.status == "succeeded"
    assert len(comparisons) == 1
    assert comparisons[0].primary_run_id == formal.id
    assert comparisons[0].comparison_run_id == run.id
