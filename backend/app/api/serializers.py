from __future__ import annotations

from typing import Any

from app.db.repositories import decode_json


def engine_run_dict(record: Any) -> dict[str, Any]:
    return {
        "id": record.id,
        "engine_type": record.engine_type,
        "run_type": record.run_type,
        "strategy_code": record.strategy_code,
        "factor_code": record.factor_code,
        "start_date": record.start_date,
        "end_date": record.end_date,
        "config": decode_json(record.config_json),
        "status": record.status,
        "result_summary": decode_json(record.result_summary_json),
        "artifact_path": record.artifact_path,
        "started_at": record.started_at,
        "completed_at": record.completed_at,
        "error_message": record.error_message,
    }


def backtest_run_dict(record: Any) -> dict[str, Any]:
    return {
        "id": record.id,
        "engine_type": record.engine_type,
        "strategy_code": record.strategy_code,
        "start_date": record.start_date,
        "end_date": record.end_date,
        "config": decode_json(record.config_json),
        "status": record.status,
        "result": decode_json(record.result_json),
        "formal_ashare_result": record.formal_ashare_result,
        "created_at": record.created_at,
        "completed_at": record.completed_at,
    }


def model_experiment_dict(record: Any) -> dict[str, Any]:
    return {
        "id": record.id,
        "experiment_code": record.experiment_code,
        "engine": record.engine,
        "model_type": record.model_type,
        "train_start": record.train_start,
        "train_end": record.train_end,
        "validation_start": record.validation_start,
        "validation_end": record.validation_end,
        "test_start": record.test_start,
        "test_end": record.test_end,
        "feature_version": record.feature_version,
        "label_definition": record.label_definition,
        "config": decode_json(record.config_json),
        "result": decode_json(record.result_json),
        "status": record.status,
        "experiment_only": record.experiment_only,
        "production_enabled": record.production_enabled,
        "created_at": record.created_at,
        "completed_at": record.completed_at,
    }


def walk_forward_dict(record: Any) -> dict[str, Any]:
    result = decode_json(record.result_json) or {}
    aggregate = dict(result.get("aggregate_metrics", {}))
    if "excess_return" not in aggregate:
        aggregate["excess_return"] = aggregate.get("median_oos_excess_return", 0.0)
    if "tradable_return" not in aggregate:
        aggregate["tradable_return"] = aggregate.get("median_oos_excess_return", 0.0)
    if "trade_count" not in aggregate:
        aggregate["trade_count"] = aggregate.get("closed_trade_count", 0)
    return {
        "id": record.id,
        "experiment_code": record.experiment_code,
        "strategy_code": record.strategy_code,
        "data_snapshot_version": record.data_snapshot_version,
        "start_date": record.start_date,
        "end_date": record.end_date,
        "config": decode_json(record.config_json),
        "result": result,
        "splits": result.get("splits", []),
        "aggregate_metrics": aggregate,
        "gate_results": result.get("gates", result.get("gate_results", {})),
        "source_engine": "ashare_daily_v1",
        "research_engine": result.get("research_engine", "vectorbt"),
        "formal_ashare_validation": result.get("formal_ashare_validation", "required"),
        "factor_version": result.get("factor_version", "transparent_factor_v1"),
        "artifact_path": record.artifact_path,
        "status": record.status,
        "lifecycle_status": record.lifecycle_status,
        "production_enabled": False,
        "created_at": record.created_at,
        "completed_at": record.completed_at,
        "error_message": record.error_message,
    }
