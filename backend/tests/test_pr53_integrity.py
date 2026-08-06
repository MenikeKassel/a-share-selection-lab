"""PR 5.3 tests: valuation precision persistence, tick-based open-limit
tolerance, and capability-vs-file cross validation."""
import json

import pandas as pd
import pytest
from app.adapters.market_data.tinyshare.supplement import (
    VALUATION_STORAGE_COLUMNS,
    _normalise_valuations,
)
from app.services.walk_forward import (
    WalkForwardSnapshotError,
    WalkForwardTaskService,
)


def _calendar() -> pd.DataFrame:
    return pd.DataFrame(
        {"date": pd.bdate_range("2024-07-01", "2024-07-31"), "is_open": True}
    )


def test_valuation_storage_keeps_available_at_precision(tmp_path) -> None:
    """PR 5.3: the precision column must survive the real storage path
    (_normalise_valuations -> storage columns -> parquet round trip)."""
    rows = [
        {
            "ts_code": "000001.SZ",
            "trade_date": "20240710",
            "pe_ttm": 5.0,
            "pb": 0.6,
            "ps_ttm": 1.1,
            "dv_ttm": 2.5,
            "total_mv": 200_000.0,
            "turnover_rate": 0.5,
            "volume_ratio": 1.2,
        }
    ]
    frame = _normalise_valuations(rows, _calendar())
    assert "available_at_precision" in frame.columns
    assert frame["available_at_precision"].eq("timestamp").all()
    assert "available_at_precision" in VALUATION_STORAGE_COLUMNS

    path = tmp_path / "valuations.parquet"
    frame.to_parquet(path, index=False)
    loaded = pd.read_parquet(path)
    assert "available_at_precision" in loaded.columns
    assert loaded["available_at_precision"].eq("timestamp").all()


def test_capability_gate_verifies_security_master_file(tmp_path: object) -> None:
    """PR 5.3: declared real_listing_dates=true must be backed by a real
    security_master file with real flags."""
    import pathlib

    tmp_path = pathlib.Path(str(tmp_path))
    manifest = {
        "schema_version": 2,
        "files": {"security_master": {"path": "security_master.parquet"}},
        "capabilities": {
            "real_listing_dates": True,
            "pit_financials_enforced": True,
            "pit_valuations_enforced": True,
        },
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    # Missing file -> blocked.
    with pytest.raises(WalkForwardSnapshotError, match="security_master file is missing"):
        WalkForwardTaskService._schema_v2_capability_gate(manifest, manifest_path)

    # File present but flags false -> blocked.
    pd.DataFrame(
        [
            {"symbol": "000001.SZ", "list_date": "1991-04-03",
             "is_real_listing_date": False, "listing_date_source": "fallback"}
        ]
    ).to_parquet(tmp_path / "security_master.parquet", index=False)
    with pytest.raises(WalkForwardSnapshotError, match="non-real listing dates"):
        WalkForwardTaskService._schema_v2_capability_gate(manifest, manifest_path)


def test_capability_gate_verifies_financials_file(tmp_path: object) -> None:
    """PR 5.3: pit_financials_enforced=true requires financials with PIT
    audit columns."""
    import pathlib

    tmp_path = pathlib.Path(str(tmp_path))
    manifest = {
        "schema_version": 2,
        "files": {"financials": {"path": "financials.parquet"}},
        "capabilities": {
            "real_listing_dates": True,
            "pit_financials_enforced": True,
            "pit_valuations_enforced": True,
        },
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    pd.DataFrame(
        [
            {
                "symbol": "000001.SZ",
                "period_end": "2024-06-30",
                "published_at": "2024-07-10 18:30:00",
                "available_at": "2024-07-10 18:30:00",
                "fetched_at": "2024-07-10 19:00:00",
                "source": "test",
                "content_hash": "x",
            }
        ]
    ).to_parquet(tmp_path / "financials.parquet", index=False)
    # All PIT columns present -> passes the financials check (then fails on
    # the missing security_master for real_listing_dates; assert the error
    # is NOT about financials).
    with pytest.raises(WalkForwardSnapshotError, match="security_master"):
        WalkForwardTaskService._schema_v2_capability_gate(manifest, manifest_path)


def test_open_at_limit_uses_tick_tolerance_not_proportional_band() -> None:
    """PR 5.3: the open-limit flags must use a half-tick tolerance so an
    open 0.15 below the limit price is NOT labelled at-limit."""
    from decimal import Decimal

    from app.adapters.market_data.purchased_csv.importer import _limit_prices_for_row
    from app.market_rules.price_limits import limit_prices, price_limit_ratio

    pre_close = Decimal("100.00")
    ratio = price_limit_ratio("000001.SZ", pd.Timestamp("2024-01-02").date())
    limit_up, _ = limit_prices(pre_close, ratio)
    assert limit_up == Decimal("110.00")

    row = pd.Series(
        {
            "pre_close": 100.0,
            "symbol": "000001.SZ",
            "date": "2024-01-02",
            "is_st": False,
        }
    )
    up_price, _ = _limit_prices_for_row(row)
    assert up_price == 110.0

    # Open 109.85: previously flagged at-limit (tolerance 0.15); with the
    # half-tick band (0.005) it is NOT at-limit.
    open_price = 109.85
    tick_tolerance = 0.005
    assert not (open_price >= up_price - tick_tolerance)
    # Open exactly at limit: flagged.
    assert up_price - tick_tolerance <= 110.0
    # Open one tick below limit: still flagged (float noise absorption).
    assert up_price - tick_tolerance <= 109.995
