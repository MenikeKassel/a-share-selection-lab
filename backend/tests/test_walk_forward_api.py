from collections.abc import Generator

from app.core.config import Settings, get_settings
from app.db.session import create_session_factory, get_db, initialize_database
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def test_walk_forward_missing_independent_snapshot_is_persisted_as_blocked(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'walk-forward.db').as_posix()}"
    initialize_database(database_url)
    factory = create_session_factory(database_url)
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        data_root=tmp_path / "data",
        artifact_root=tmp_path / "artifacts",
    )
    settings.data_root.mkdir(parents=True, exist_ok=True)
    app = create_app()

    def override_db() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/walk-forward-experiments",
            json={
                "experiment_code": "missing-snapshot-test",
                "snapshot_manifest_path": "data/raw/imports/ashare-2018-2025-v1/manifest.json",
            },
        )
        assert response.status_code == 202
        payload = response.json()
        assert payload["status"] == "blocked"
        assert payload["lifecycle_status"] == "experimental"
        assert payload["production_enabled"] is False
        assert payload["gate_results"]["all_passed"] is False
        artifact_root = settings.artifact_root / "walk-forward" / "missing-snapshot-test"
        assert (artifact_root / "REPORT.md").exists()
        assert (artifact_root / "manifest.json").exists()
        listed = client.get("/api/v1/walk-forward-experiments")
        assert listed.status_code == 200
        assert listed.json()[0]["experiment_code"] == "missing-snapshot-test"
