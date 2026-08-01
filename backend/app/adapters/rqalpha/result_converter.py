from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pandas as pd


def import_rqalpha_result(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".json":
        return cast(dict[str, Any], pd.read_json(path, typ="series").to_dict())
    if path.suffix.lower() == ".csv":
        frame = pd.read_csv(path)
        return {"trades": frame.to_dict(orient="records"), "performance": {}}
    if path.suffix.lower() in {".pkl", ".pickle"}:
        # This path must point to the artifact created by this local RQAlpha run.
        payload = pd.read_pickle(path)
        if not isinstance(payload, dict):
            raise ValueError("RQAlpha pickle does not contain a result dictionary")
        trades = payload.get("trades", [])
        if isinstance(trades, pd.DataFrame):
            trades = trades.to_dict(orient="records")
        summary = payload.get("summary", payload.get("performance", {}))
        if isinstance(summary, pd.Series):
            summary = summary.to_dict()
        performance = dict(summary) if isinstance(summary, dict) else {}
        if "total_returns" in performance and "total_return" not in performance:
            performance["total_return"] = performance["total_returns"]
        return {
            "trades": trades if isinstance(trades, list) else [],
            "performance": performance,
        }
    raise ValueError(f"unsupported RQAlpha result artifact: {path.suffix}")


def compare_engine_results(
    self_result: dict[str, Any], rqalpha_result: dict[str, Any]
) -> dict[str, Any]:
    self_performance = self_result.get("performance", {})
    rq_performance = rqalpha_result.get("performance", {})
    self_return = float(self_performance.get("tradable_return", 0.0))
    rq_return = float(rq_performance.get("total_return", 0.0))
    self_trades = self_result.get("trades", [])
    rq_trades = rqalpha_result.get("trades", [])
    self_symbols = [str(item.get("symbol")) for item in self_trades]
    rq_symbols = [str(item.get("symbol")) for item in rq_trades]
    unmatched = sorted(set(self_symbols).symmetric_difference(rq_symbols))
    return {
        "self_engine_return": self_return,
        "rqalpha_return": rq_return,
        "return_difference": round(self_return - rq_return, 12),
        "trade_count_difference": len(self_trades) - len(rq_trades),
        "execution_difference": self_result.get("execution_failures", []),
        "fee_difference": round(
            float(self_performance.get("total_fees", 0.0))
            - float(rq_performance.get("total_fees", 0.0)),
            12,
        ),
        "unmatched_trades": unmatched,
    }
