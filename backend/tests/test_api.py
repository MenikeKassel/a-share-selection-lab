from collections.abc import Generator

import pandas as pd
from app.core.config import Settings, get_settings
from app.db.session import create_session_factory, get_db, initialize_database
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def test_engine_status_and_formal_backtest_api(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'api.db').as_posix()}"
    initialize_database(database_url)
    factory = create_session_factory(database_url)
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        data_root=tmp_path / "data",
        artifact_root=tmp_path / "artifacts",
    )
    app = create_app()

    def override_db() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings

    settings.data_root.mkdir(parents=True, exist_ok=True)
    market_path = settings.data_root / "market.csv"
    signal_path = settings.data_root / "signals.csv"
    pd.DataFrame(
        [
            {
                "date": "2026-01-02",
                "symbol": "A",
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 10.0,
                "volume": 100_000,
                "industry": "测试",
            },
            {
                "date": "2026-01-05",
                "symbol": "A",
                "open": 10.0,
                "high": 11.1,
                "low": 9.9,
                "close": 11.0,
                "volume": 100_000,
                "industry": "测试",
            },
        ]
    ).to_csv(market_path, index=False)
    pd.DataFrame([{"signal_date": "2026-01-02", "symbol": "A", "score": 90.0}]).to_csv(
        signal_path, index=False
    )

    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        engines = client.get("/api/v1/engines/status")
        backtest = client.post(
            "/api/v1/backtests",
            json={
                "strategy_code": "trend_quality_v1",
                "start_date": "2026-01-02",
                "end_date": "2026-01-05",
                "top_n": 1,
                "holding_period": 5,
                "rebalance_frequency": "daily",
                "commission_rate": 0.0003,
                "minimum_commission": 5,
                "stamp_tax_rate": 0.0005,
                "slippage_bps": 0,
                "benchmark_symbol": "000300.SH",
                "initial_cash": 100000,
                "max_stock_weight": 1,
                "max_industry_weight": 1,
                "market_data_path": str(market_path),
                "signal_path": str(signal_path),
            },
        )

    assert health.status_code == 200
    assert health.json()["automatic_ordering"] is False
    assert engines.status_code == 200
    assert {item["engine_type"] for item in engines.json()} == {
        "alphalens",
        "vectorbt",
        "rqalpha",
        "qlib",
    }
    assert backtest.status_code == 201
    assert backtest.json()["formal_ashare_result"] is True
    assert backtest.json()["engine_type"] == "ashare_daily_v1"
    assert backtest.json()["result"]["metadata"]["formal_ashare_result"] is True
