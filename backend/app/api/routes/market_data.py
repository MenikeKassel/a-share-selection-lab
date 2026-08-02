from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.schemas import MarketDataSnapshotRequest
from app.core.config import Settings, get_settings
from app.db.models import MarketDataSnapshot
from app.db.repositories import MarketDataSnapshotRepository, decode_json
from app.db.session import get_db
from app.services.market_data import MarketDataSnapshotError, MarketDataSnapshotService

router = APIRouter(tags=["market-data"])


@router.get("/data-providers")
def list_data_providers(settings: Settings = Depends(get_settings)) -> list[dict[str, Any]]:
    service = MarketDataSnapshotService(settings)
    return [_provider_dict(service)]


@router.get("/data-providers/freestockdb/status")
def freestockdb_status(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return _provider_dict(MarketDataSnapshotService(settings))


@router.post("/market-data-snapshots", status_code=status.HTTP_201_CREATED)
def create_market_data_snapshot(
    payload: MarketDataSnapshotRequest,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if not settings.freestockdb_enabled:
        raise HTTPException(status_code=503, detail="FreeStockDB provider is disabled")
    service = MarketDataSnapshotService(settings)
    try:
        record = service.create_snapshot(
            repository=MarketDataSnapshotRepository(session),
            start_date=payload.start_date,
            end_date=payload.end_date,
            lookback_days=payload.lookback_days,
        )
    except (MarketDataSnapshotError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return market_data_snapshot_dict(record)


@router.get("/market-data-snapshots")
def list_market_data_snapshots(
    session: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return [
        market_data_snapshot_dict(item)
        for item in MarketDataSnapshotRepository(session).list()
    ]


@router.get("/market-data-snapshots/{snapshot_id}")
def get_market_data_snapshot(
    snapshot_id: int,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    record = MarketDataSnapshotRepository(session).get(snapshot_id)
    if record is None:
        raise HTTPException(status_code=404, detail="market data snapshot not found")
    return market_data_snapshot_dict(record)


def market_data_snapshot_dict(record: MarketDataSnapshot) -> dict[str, Any]:
    return {
        "id": record.id,
        "provider_code": record.provider_code,
        "snapshot_code": record.snapshot_code,
        "status": record.status,
        "start_date": record.start_date,
        "end_date": record.end_date,
        "daily_latest_date": record.daily_latest_date,
        "minute_latest_date": record.minute_latest_date,
        "daily_row_count": record.daily_row_count,
        "daily_symbol_count": record.daily_symbol_count,
        "daily_coverage_ratio": record.daily_coverage_ratio,
        "minute_coverage_ratio": record.minute_coverage_ratio,
        "manifest_path": record.manifest_path,
        "daily_path": record.daily_path,
        "adjustment_path": record.adjustment_path,
        "minute_directory": record.minute_directory,
        "config": decode_json(record.config_json),
        "metadata": decode_json(record.metadata_json),
        "walk_forward_eligible": record.walk_forward_eligible,
        "created_at": record.created_at,
        "completed_at": record.completed_at,
        "error_message": record.error_message,
    }


def _provider_dict(service: MarketDataSnapshotService) -> dict[str, Any]:
    status_value = service.status()
    return {
        "provider_code": status_value["provider_code"],
        "configured": status_value["configured"],
        "reachable": status_value["reachable"],
        "endpoint": status_value["endpoint"],
        "read_only": status_value["read_only"],
        "daily_latest_date": status_value["daily_latest_date"],
        "minute_latest_date": status_value["minute_latest_date"],
        "daily_instrument_count": status_value["daily_instrument_count"],
        "minute_instrument_count": status_value["minute_instrument_count"],
        "capabilities": status_value["capabilities"],
        "limitations": status_value["limitations"],
        "checked_at": status_value["checked_at"],
        "error": status_value["error"],
    }
