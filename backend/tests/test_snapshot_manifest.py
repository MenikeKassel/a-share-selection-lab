from __future__ import annotations

import hashlib
import json

import pandas as pd
import pytest
from app.data.snapshots import (
    SnapshotManifestError,
    SnapshotManifestValidator,
    audit_snapshot_files,
    validate_point_in_time_frame,
    validate_point_in_time_path,
    validate_snapshot_manifest,
)


def _write_manifest(tmp_path, **overrides):
    daily = tmp_path / "daily.parquet"
    daily.write_bytes(b"placeholder")
    payload = {
        "snapshot_id": "ashare-2018-2025-v1",
        "immutable": True,
        "audit_valid": True,
        "status": "ready",
        "coverage_ratio": 0.99,
        "point_in_time_cutoff": "18:30",
        "files": {"daily": daily.name},
        **overrides,
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    return manifest


def test_manifest_validator_resolves_files_and_keeps_snapshot_id(tmp_path) -> None:
    manifest = _write_manifest(tmp_path)

    result = validate_snapshot_manifest(manifest)

    assert result.snapshot_id == "ashare-2018-2025-v1"
    assert result.coverage_ratio == pytest.approx(0.99)
    assert result.file("daily") == (tmp_path / "daily.parquet").resolve()


def test_manifest_validator_blocks_low_coverage_and_mutable_snapshots(tmp_path) -> None:
    with pytest.raises(SnapshotManifestError, match="coverage ratio"):
        validate_snapshot_manifest(_write_manifest(tmp_path, coverage_ratio=0.94))

    mutable_dir = tmp_path / "mutable"
    mutable_dir.mkdir()
    with pytest.raises(SnapshotManifestError, match="not immutable"):
        validate_snapshot_manifest(_write_manifest(mutable_dir, immutable=False))


def test_manifest_validator_rejects_paths_outside_snapshot_directory(tmp_path) -> None:
    outside = tmp_path / "outside.parquet"
    outside.write_bytes(b"not in snapshot")
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    manifest = _write_manifest(snapshot, files={"daily": str(outside)})

    with pytest.raises(SnapshotManifestError, match="outside"):
        SnapshotManifestValidator().load(manifest)


def test_point_in_time_frame_requires_ordered_audit_timestamps() -> None:
    valid = pd.DataFrame(
        [
            {
                "period_end": "2025-09-30",
                "published_at": "2025-10-30 17:30:00",
                "available_at": "2025-10-30 17:31:00",
                "fetched_at": "2025-10-30 17:35:00",
                "source": "test",
                "content_hash": "sha256:test",
            }
        ]
    )
    validate_point_in_time_frame(valid)

    invalid = valid.assign(available_at="2025-10-30 17:29:00")
    with pytest.raises(SnapshotManifestError, match="earlier than published"):
        validate_point_in_time_frame(invalid)


def test_point_in_time_parquet_validation_is_incremental(tmp_path, monkeypatch) -> None:
    path = tmp_path / "valuations.parquet"
    pd.DataFrame(
        [
            {
                "period_end": "2025-01-02",
                "published_at": "2025-01-02 18:00:00",
                "available_at": "2025-01-02 18:30:00",
                "fetched_at": "2025-01-02 19:00:00",
                "source": "test",
                "content_hash": "one",
            },
            {
                "period_end": "2025-01-03",
                "published_at": "2025-01-03 18:00:00",
                "available_at": "2025-01-03 18:30:00",
                "fetched_at": "2025-01-03 19:00:00",
                "source": "test",
                "content_hash": "two",
            },
        ]
    ).to_parquet(path, row_group_size=1, index=False)

    monkeypatch.setattr(
        "app.data.snapshots._read_tabular",
        lambda _path: pytest.fail("large parquet must not use the full-frame reader"),
    )

    validate_point_in_time_path(path, batch_size=1)


def test_snapshot_file_audit_checks_real_daily_bars_and_hash(tmp_path) -> None:
    daily = tmp_path / "daily.csv"
    pd.DataFrame(
        [
            {
                "date": "2025-01-02",
                "symbol": "A",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10,
                "volume": 1000,
                "amount": 10000,
            },
        ]
    ).to_csv(daily, index=False)
    digest = hashlib.sha256(daily.read_bytes()).hexdigest()
    manifest_path = _write_manifest(
        tmp_path,
        files={"daily": {"path": daily.name, "sha256": digest}},
        expected_universe_size=1,
    )
    snapshot = validate_snapshot_manifest(manifest_path)
    audit = audit_snapshot_files(snapshot)
    assert audit["audit_valid"] is True
    assert audit["daily_coverage_ratio"] == pytest.approx(1.0)

    daily.write_text(daily.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(SnapshotManifestError, match="content hash mismatch"):
        audit_snapshot_files(snapshot)


def test_snapshot_coverage_excludes_explicitly_suspended_symbols(tmp_path) -> None:
    daily = tmp_path / "daily.csv"
    pd.DataFrame(
        [
            {
                "date": "2025-01-02",
                "symbol": "A",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10,
                "volume": 1000,
                "amount": 10000,
            },
            {
                "date": "2025-01-02",
                "symbol": "B",
                "open": 20,
                "high": 21,
                "low": 19,
                "close": 20,
                "volume": 1000,
                "amount": 20000,
            },
        ]
    ).to_csv(daily, index=False)
    universe = tmp_path / "universe.csv"
    pd.DataFrame(
        [
            {"symbol": "A", "list_date": "2020-01-01", "delist_date": ""},
            {"symbol": "B", "list_date": "2020-01-01", "delist_date": ""},
        ]
    ).to_csv(universe, index=False)
    suspensions = tmp_path / "suspensions.csv"
    pd.DataFrame([{"date": "2025-01-02", "symbol": "B"}]).to_csv(suspensions, index=False)

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    manifest_path = _write_manifest(
        tmp_path,
        files={
            "daily": {"path": daily.name, "sha256": digest(daily)},
            "universe": {"path": universe.name, "sha256": digest(universe)},
            "suspensions": {"path": suspensions.name, "sha256": digest(suspensions)},
        },
    )
    snapshot = validate_snapshot_manifest(manifest_path)
    audit = audit_snapshot_files(snapshot)

    assert audit["daily_coverage_ratio"] == pytest.approx(1.0)
