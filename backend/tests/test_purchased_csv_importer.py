from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from app.adapters.market_data.purchased_csv import (
    PurchasedCsvImportError,
    PurchasedCsvSnapshotImporter,
)


def _write_source(root: Path) -> Path:
    source = root / "source"
    source.mkdir()
    rows = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "trade_date": 20170103,
                "name": "平安银行",
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "pre_close": 10.0,
                "change": 0.2,
                "pct_chg": 2.0,
                "vol": 2.0,
                "amount": 3.0,
                "adj_factor": 2.0,
                "first_adj": 1.0,
            },
            {
                "ts_code": "000001.SZ",
                "trade_date": 20170104,
                "name": "平安银行",
                "open": 10.1,
                "high": 10.6,
                "low": 10.0,
                "close": 10.4,
                "pre_close": 10.2,
                "change": 0.2,
                "pct_chg": 1.96,
                "vol": 3.0,
                "amount": 4.0,
                "adj_factor": 2.0,
                "first_adj": 1.0,
            },
        ]
    )
    rows.to_csv(source / "000001.SZ.csv", index=False)
    return source


def test_importer_normalizes_units_and_causal_adjustment(tmp_path: Path) -> None:
    source = _write_source(tmp_path)
    result = PurchasedCsvSnapshotImporter(
        source_dir=source,
        snapshot_root=tmp_path / "snapshots",
        snapshot_id="test-snapshot",
        start_date=pd.Timestamp("2017-01-01").date(),
        end_date=pd.Timestamp("2017-01-04").date(),
    ).run()

    frame = pd.read_parquet(result.daily_path)
    assert frame["volume"].tolist() == [200.0, 300.0]
    assert frame["amount"].tolist() == [3000.0, 4000.0]
    assert frame["adj_close"].tolist() == [20.4, 20.8]
    assert frame["limit_source"].eq("derived_from_raw_daily").all()
    assert result.manifest_path.exists()
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["source"]["file_hashes"]

    with pytest.raises(PurchasedCsvImportError, match="already exists"):
        PurchasedCsvSnapshotImporter(
            source_dir=source,
            snapshot_root=tmp_path / "snapshots",
            snapshot_id="test-snapshot",
            start_date=pd.Timestamp("2017-01-01").date(),
            end_date=pd.Timestamp("2017-01-04").date(),
        ).run()
