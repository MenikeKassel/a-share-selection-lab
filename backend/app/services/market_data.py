from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from app.adapters.market_data.base import (
    DailyBarsRequest,
    MarketDataProviderStatus,
    MinuteBarsRequest,
)
from app.adapters.market_data.freestockdb import FreeStockDBHttpAdapter
from app.adapters.market_data.freestockdb.adapter import apply_adjustment_factors
from app.core.config import Settings
from app.data.contracts import validate_market_frame
from app.db.models import MarketDataSnapshot
from app.db.repositories import MarketDataSnapshotRepository


class MarketDataSnapshotError(RuntimeError):
    pass


class MarketDataSnapshotService:
    provider_code = "freestockdb"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def provider(self) -> FreeStockDBHttpAdapter:
        return FreeStockDBHttpAdapter(
            base_url=self.settings.freestockdb_base_url,
            connect_timeout_seconds=self.settings.freestockdb_connect_timeout_seconds,
            read_timeout_seconds=self.settings.freestockdb_read_timeout_seconds,
            max_concurrency=self.settings.freestockdb_max_concurrency,
        )

    def status(self) -> dict[str, Any]:
        if not self.settings.freestockdb_enabled:
            return asdict(
                MarketDataProviderStatus(
                    provider_code=self.provider_code,
                    configured=False,
                    reachable=False,
                    endpoint=self.settings.freestockdb_base_url,
                    read_only=True,
                    limitations=("provider_disabled_by_configuration",),
                    error="FreeStockDB provider is disabled",
                )
            )
        return asdict(self.provider().status())

    def create_snapshot(
        self,
        *,
        repository: MarketDataSnapshotRepository,
        start_date: date | None = None,
        end_date: date | None = None,
        lookback_days: int | None = None,
    ) -> MarketDataSnapshot:
        if not self.settings.freestockdb_enabled:
            raise MarketDataSnapshotError("FreeStockDB provider is disabled")
        provider = self.provider()
        provider_status = provider.status()
        if not provider_status.reachable or provider_status.daily_latest_date is None:
            raise MarketDataSnapshotError(provider_status.error or "FreeStockDB is unavailable")
        actual_end = end_date or provider_status.daily_latest_date
        actual_start = start_date or (
            actual_end
            - timedelta(
                days=lookback_days or self.settings.freestockdb_default_lookback_days
            )
        )
        if actual_start > actual_end:
            raise ValueError("snapshot start_date must not be after end_date")
        snapshot_code = f"freestockdb-{actual_end:%Y%m%d}-{uuid.uuid4().hex[:12]}"
        record = repository.create(
            provider_code=self.provider_code,
            snapshot_code=snapshot_code,
            start_date=actual_start,
            end_date=actual_end,
            config={
                "provider_code": self.provider_code,
                "base_url": self.settings.freestockdb_base_url,
                "start_date": actual_start.isoformat(),
                "end_date": actual_end.isoformat(),
                "volume_unit": "shares",
                "price_basis": {"factor": "qfq", "execution": "raw"},
            },
        )
        root = self.settings.artifact_root / "market-data" / self.provider_code / snapshot_code
        root.mkdir(parents=True, exist_ok=False)
        daily_path = root / "daily.parquet"
        adjustment_path = root / "adjustment_factors.parquet"
        manifest_path = root / "manifest.json"
        try:
            daily = provider.fetch_daily(DailyBarsRequest(actual_start, actual_end))
            if daily.empty:
                raise MarketDataSnapshotError("FreeStockDB returned no daily bars")
            quality = validate_market_frame(daily, granularity="daily")
            if not quality.valid:
                raise MarketDataSnapshotError(
                    "daily provider data failed validation: "
                    f"missing={quality.missing_columns}, duplicates={quality.duplicate_rows}, "
                    f"invalid_prices={quality.invalid_price_rows}"
                )
            factors = provider.fetch_adjustment_factors(daily["symbol"].astype(str).unique())
            daily = apply_adjustment_factors(daily, factors)
            daily.to_parquet(daily_path, index=False)
            factors.to_parquet(adjustment_path, index=False)
            expected_symbols = {
                symbol
                for symbol in provider.fetch_daily_cross_section(actual_end)["symbol"].astype(str)
                if provider.is_stock_symbol(symbol)
            }
            latest = daily.loc[pd.to_datetime(daily["date"]).dt.date == actual_end]
            coverage = len(set(latest["symbol"].astype(str))) / max(len(expected_symbols), 1)
            metadata = {
                "immutable": True,
                "status": "ready",
                "audit_valid": quality.valid,
                "walk_forward_eligible": False,
                "information_cutoff": "18:30",
                "expected_universe_size": len(expected_symbols),
                "daily_coverage_ratio": coverage,
                "provider_status": asdict(provider_status),
                "limitations": list(provider_status.limitations),
                "files": {
                    "daily": {"path": daily_path.name, "sha256": _sha256(daily_path)},
                    "adjustment_factors": {
                        "path": adjustment_path.name,
                        "sha256": _sha256(adjustment_path),
                    },
                },
                "content_hash": _frame_hash(daily),
            }
            manifest_path.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            finished = repository.finish(
                record,
                status="ready" if coverage >= self.settings.min_daily_coverage_ratio else "blocked",
                metadata=metadata,
                manifest_path=str(manifest_path),
                daily_path=str(daily_path),
                adjustment_path=str(adjustment_path),
                daily_latest_date=pd.to_datetime(daily["date"]).max().date(),
                minute_latest_date=provider_status.minute_latest_date,
                daily_row_count=len(daily),
                daily_symbol_count=int(daily["symbol"].nunique()),
                daily_coverage_ratio=coverage,
                minute_coverage_ratio=0.0,
                walk_forward_eligible=False,
                error_message=(
                    None
                    if coverage >= self.settings.min_daily_coverage_ratio
                    else (
                        f"daily coverage {coverage:.2%} is below "
                        f"{self.settings.min_daily_coverage_ratio:.2%}"
                    )
                ),
            )
            return finished
        except Exception as error:
            repository.finish(
                record,
                status="failed",
                metadata={"provider_status": asdict(provider_status)},
                error_message=str(error),
            )
            raise

    def load_daily(self, record: MarketDataSnapshot) -> pd.DataFrame:
        if record.status != "ready" or not record.daily_path:
            raise MarketDataSnapshotError("market data snapshot is not ready")
        path = Path(record.daily_path).resolve()
        if not path.is_file():
            raise MarketDataSnapshotError(f"snapshot daily file does not exist: {path}")
        return pd.read_parquet(path)

    def minute_loader(
        self,
        record: MarketDataSnapshot,
    ) -> Callable[[list[str], date], dict[str, pd.DataFrame]]:
        provider = self.provider()
        cache_root = (
            self.settings.data_root
            / "runtime"
            / "freestockdb-minute"
            / record.snapshot_code
        )
        cache_root.mkdir(parents=True, exist_ok=True)

        def load(symbols: list[str], selection_date: date) -> dict[str, pd.DataFrame]:
            output: dict[str, pd.DataFrame] = {}
            missing: list[str] = []
            for symbol in symbols:
                cache_path = cache_root / f"{symbol}.parquet"
                if cache_path.exists():
                    try:
                        output[symbol] = pd.read_parquet(cache_path)
                        continue
                    except Exception:
                        cache_path.unlink(missing_ok=True)
                missing.append(symbol)
            if missing and provider.status().reachable:
                start = selection_date - timedelta(
                    days=self.settings.freestockdb_minute_lookback_days
                )
                fetched = provider.fetch_minute(
                    MinuteBarsRequest(
                        start_timestamp=f"{start:%Y%m%d}000000",
                        end_timestamp=f"{selection_date:%Y%m%d}235959",
                        symbols=tuple(missing),
                    )
                )
                for symbol, frame in fetched.items():
                    frame.to_parquet(cache_root / f"{symbol}.parquet", index=False)
                    output[symbol] = frame
            return output

        return load


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _frame_hash(frame: pd.DataFrame) -> str:
    stable = frame.sort_values([column for column in ("date", "symbol") if column in frame]).copy()
    payload = stable.to_json(orient="records", date_format="iso", double_precision=10).encode()
    return hashlib.sha256(payload).hexdigest()
