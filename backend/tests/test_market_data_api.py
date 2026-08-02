from collections.abc import Generator

from app.core.config import Settings, get_settings
from app.db.session import create_session_factory, get_db, initialize_database
from app.main import create_app
from app.services.market_data import MarketDataSnapshotService
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def test_market_data_provider_status_and_snapshot_list_are_readable_without_provider(
    tmp_path, monkeypatch
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'market-data.db').as_posix()}"
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
    monkeypatch.setattr(
        MarketDataSnapshotService,
        "status",
        lambda _self: {
            "provider_code": "freestockdb",
            "configured": True,
            "reachable": False,
            "endpoint": "http://127.0.0.1:7899",
            "read_only": True,
            "daily_latest_date": None,
            "minute_latest_date": None,
            "daily_instrument_count": 0,
            "minute_instrument_count": 0,
            "capabilities": [],
            "limitations": ["service_unavailable"],
            "checked_at": None,
            "error": "offline",
        },
    )

    with TestClient(app) as client:
        provider = client.get("/api/v1/data-providers/freestockdb/status")
        snapshots = client.get("/api/v1/market-data-snapshots")

    assert provider.status_code == 200
    assert provider.json()["read_only"] is True
    assert provider.json()["reachable"] is False
    assert snapshots.status_code == 200
    assert snapshots.json() == []


def test_market_data_snapshot_creation_is_disabled_cleanly(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'market-data-disabled.db').as_posix()}"
    initialize_database(database_url)
    factory = create_session_factory(database_url)
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        data_root=tmp_path / "data",
        artifact_root=tmp_path / "artifacts",
        freestockdb_enabled=False,
    )
    app = create_app()

    def override_db() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/market-data-snapshots",
            json={"provider_code": "freestockdb", "lookback_days": 30},
        )

    assert response.status_code == 503
    assert "disabled" in response.json()["detail"]
