from __future__ import annotations

import json
import math
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.adapters.io import read_tabular
from app.api.schemas import ReviewRunRequest, SelectionRunRequest
from app.core.config import Settings, get_settings
from app.data.freshness import estimate_expected_universe_size
from app.data.trading_calendar import (
    AShareTradingCalendar,
    TradingCalendarRangeError,
)
from app.db.models import CandidateReview, DataQualitySnapshot, SelectionSnapshot
from app.db.session import get_db
from app.selection.pipeline import DailySelectionPipeline
from app.selection.review import AutomaticReviewService
from app.selection.snapshots import SelectionSnapshotRepository

router = APIRouter(tags=["selection-and-review"])


@router.post("/selections/run", status_code=status.HTTP_201_CREATED)
def run_selection(
    payload: SelectionRunRequest,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    daily = read_tabular(_data_path(payload.daily_path, settings))
    daily_dates = pd.to_datetime(daily["date"]).dt.date.unique().tolist()
    now = datetime.now(ZoneInfo(settings.timezone))
    try:
        expected_trade_date = (
            payload.expected_trade_date or AShareTradingCalendar().expected_latest_trade_date(now)
        )
    except TradingCalendarRangeError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if expected_trade_date not in daily_dates:
        daily_dates.append(expected_trade_date)
    expected_size = estimate_expected_universe_size(
        daily,
        configured_size=(payload.expected_universe_size or settings.expected_universe_size),
    )
    minute_data = _read_minute_directory(payload.minute_directory, settings)
    result = DailySelectionPipeline(
        artifact_root=settings.artifact_root,
        snapshot_repository=SelectionSnapshotRepository(session),
        min_daily_coverage_ratio=settings.min_daily_coverage_ratio,
    ).run(
        daily=daily,
        trading_dates=daily_dates,
        now=now,
        expected_universe_size=expected_size,
        minute_data=minute_data,
        minute_volume_unit=payload.minute_volume_unit,
        financials=_optional_frame(payload.financial_path, settings),
        valuations=_optional_frame(payload.valuation_path, settings),
        benchmark=_optional_frame(payload.benchmark_path, settings),
        industry_rps=_optional_frame(payload.industry_rps_path, settings),
    )
    _save_quality_snapshot(session, result, now)
    return asdict(result)


@router.get("/selections/latest")
def latest_selection(
    strategy_code: str | None = None,
    session: Session = Depends(get_db),
) -> dict[str, object]:
    record = SelectionSnapshotRepository(session).latest(strategy_code)
    if record is None:
        raise HTTPException(status_code=404, detail="selection snapshot not found")
    return _selection_snapshot_dict(record)


@router.get("/selections")
def list_selections(
    session: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return [_selection_snapshot_dict(item) for item in SelectionSnapshotRepository(session).list()]


@router.get("/data-quality/latest")
def latest_data_quality(
    session: Session = Depends(get_db),
) -> dict[str, object]:
    record = session.scalar(
        select(DataQualitySnapshot)
        .order_by(desc(DataQualitySnapshot.as_of_date), desc(DataQualitySnapshot.id))
        .limit(1)
    )
    if record is None:
        raise HTTPException(status_code=404, detail="data-quality snapshot not found")
    return {
        "id": record.id,
        "as_of_date": record.as_of_date,
        "expected_latest_trade_date": record.expected_latest_trade_date,
        "daily_market_max_date": record.daily_market_max_date,
        "minute_market_max_date": record.minute_market_max_date,
        "daily_coverage_ratio": record.daily_coverage_ratio,
        "minute_coverage_ratio": record.minute_coverage_ratio,
        "selection_status": record.selection_status,
        "details": json.loads(record.details_json),
        "created_at": record.created_at,
    }


@router.post("/reviews/run", status_code=status.HTTP_201_CREATED)
def run_review(
    payload: ReviewRunRequest,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    snapshot = session.get(SelectionSnapshot, payload.snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="selection snapshot not found")
    candidates = json.loads(snapshot.candidates_json)
    reviews = AutomaticReviewService().calculate(
        candidates=candidates,
        market=read_tabular(_data_path(payload.market_data_path, settings)),
        horizons=payload.horizons,
        benchmark_symbol=payload.benchmark_symbol,
    )
    summary = AutomaticReviewService().summarize(reviews)
    for row in reviews.to_dict(orient="records"):
        safe_row = {
            key: (None if isinstance(value, float) and not math.isfinite(value) else value)
            for key, value in row.items()
        }
        existing = session.scalar(
            select(CandidateReview).where(
                CandidateReview.snapshot_id == payload.snapshot_id,
                CandidateReview.symbol == safe_row["symbol"],
                CandidateReview.horizon == safe_row["horizon"],
            )
        )
        if existing is None:
            session.add(
                CandidateReview(
                    snapshot_id=payload.snapshot_id,
                    symbol=str(safe_row["symbol"]),
                    horizon=int(cast(Any, safe_row["horizon"])),
                    metrics_json=json.dumps(
                        safe_row,
                        ensure_ascii=False,
                        default=str,
                        allow_nan=False,
                    ),
                )
            )
    session.commit()
    path = (
        settings.artifact_root / "reviews" / str(payload.snapshot_id) / "candidate-reviews.parquet"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    artifact_frame = reviews.copy()
    for column in artifact_frame:
        if artifact_frame[column].map(lambda value: isinstance(value, (dict, list))).any():
            artifact_frame[column] = artifact_frame[column].map(
                lambda value: (
                    json.dumps(
                        value,
                        ensure_ascii=False,
                        default=str,
                        allow_nan=False,
                    )
                    if isinstance(value, (dict, list))
                    else value
                )
            )
    artifact_frame.to_parquet(path, index=False)
    summary_path = path.with_name("review-summary.json")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, default=str, allow_nan=False, indent=2),
        encoding="utf-8",
    )
    return {
        "snapshot_id": payload.snapshot_id,
        "review_count": len(reviews),
        "artifact_path": str(path),
        "summary_artifact_path": str(summary_path),
        "horizons": payload.horizons,
        "summary": summary,
    }


def _read_minute_directory(
    path_value: str | None, settings: Settings
) -> dict[str, pd.DataFrame] | None:
    if not path_value:
        return None
    directory = _data_path(path_value, settings)
    if not directory.is_dir():
        raise HTTPException(status_code=422, detail="minute_directory is not a directory")
    output: dict[str, pd.DataFrame] = {}
    for path in [*directory.glob("*.parquet"), *directory.glob("*.csv")]:
        output[path.stem] = read_tabular(path)
    return output


def _optional_frame(path_value: str | None, settings: Settings) -> pd.DataFrame | None:
    return read_tabular(_data_path(path_value, settings)) if path_value else None


def _data_path(path_value: str, settings: Settings) -> Path:
    path = Path(path_value).expanduser().resolve()
    if not path.is_relative_to(settings.data_root.resolve()):
        raise HTTPException(
            status_code=422,
            detail="input path is outside ASHARE_DATA_ROOT",
        )
    if not path.exists():
        raise HTTPException(status_code=422, detail=f"input path not found: {path}")
    return path


def _selection_snapshot_dict(record: SelectionSnapshot) -> dict[str, object]:
    return {
        "id": record.id,
        "selection_date": record.selection_date,
        "strategy_code": record.strategy_code,
        "strategy_version": record.strategy_version,
        "factor_version": record.factor_version,
        "data_snapshot_version": record.data_snapshot_version,
        "selection_status": record.selection_status,
        "candidates": json.loads(record.candidates_json),
        "artifact_path": record.artifact_path,
        "created_at": record.created_at,
    }


def _save_quality_snapshot(
    session: Session,
    result: Any,
    now: datetime,
) -> None:
    freshness = result.freshness
    existing = session.scalar(
        select(DataQualitySnapshot).where(DataQualitySnapshot.as_of_date == now.date())
    )
    details = {
        "message": result.message,
        "candidate_count": len(result.candidates),
        "hard_gate_rejection_count": len(result.rejected_candidates),
        "minute_confirmation": (
            freshness.minute_confirmation if freshness is not None else "unavailable"
        ),
        "data_confidence": (freshness.data_confidence if freshness is not None else "blocked"),
    }
    values = {
        "expected_latest_trade_date": (
            freshness.expected_latest_trade_date if freshness is not None else None
        ),
        "daily_market_max_date": (
            freshness.daily_market_max_date if freshness is not None else result.selection_date
        ),
        "minute_market_max_date": (
            freshness.minute_market_max_date if freshness is not None else None
        ),
        "daily_coverage_ratio": (freshness.daily_coverage_ratio if freshness is not None else 0.0),
        "minute_coverage_ratio": (
            freshness.minute_coverage_ratio if freshness is not None else 0.0
        ),
        "selection_status": result.status,
        "details_json": json.dumps(details, ensure_ascii=False),
    }
    if existing is None:
        session.add(DataQualitySnapshot(as_of_date=now.date(), **values))
    else:
        for key, value in values.items():
            setattr(existing, key, value)
    session.commit()
