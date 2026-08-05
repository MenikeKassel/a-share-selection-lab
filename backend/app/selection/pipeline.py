from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from app.data.contracts import UNAVAILABLE_MICROSTRUCTURE, validate_market_frame
from app.data.freshness import DataFreshnessGate, FreshnessInput, FreshnessResult
from app.research.factors.calculator import DailyFactorCalculator
from app.research.factors.catalog import (
    default_factor_definitions,
    default_risk_definitions,
)
from app.research.factors.pipeline import FactorPipeline, PipelineConfig
from app.research.minute.confirmation import MinuteConfirmationAnalyzer
from app.research.pa import PriceActionAnalyzer
from app.research.scoring import CandidateScorer
from app.research.wyckoff import WyckoffCandidateDetector
from app.selection.snapshots import (
    SelectionSnapshotRepository,
    data_snapshot_version,
    write_candidate_artifact,
)
from app.selection.strategies import STRATEGY_LABELS, classify_candidate


@dataclass(frozen=True, slots=True)
class SelectionRunResult:
    status: str
    selection_date: date | None
    freshness: FreshnessResult | None
    candidates: list[dict[str, Any]]
    rejected_candidates: list[dict[str, Any]]
    strategy_pools: dict[str, list[dict[str, Any]]]
    snapshot_ids: list[int]
    message: str


class DailySelectionPipeline:
    strategy_version = "1.0.0"
    factor_version = "transparent_factor_v1"

    def __init__(
        self,
        *,
        artifact_root: Path,
        snapshot_repository: SelectionSnapshotRepository | None = None,
        min_daily_coverage_ratio: float = 0.95,
    ) -> None:
        self.artifact_root = artifact_root
        self.snapshot_repository = snapshot_repository
        self.freshness_gate = DataFreshnessGate(min_daily_coverage_ratio=min_daily_coverage_ratio)

    def run(
        self,
        *,
        daily: pd.DataFrame,
        trading_dates: list[date],
        now: datetime,
        expected_universe_size: int,
        minute_data: dict[str, pd.DataFrame] | None = None,
        minute_loader: Callable[[list[str], date], dict[str, pd.DataFrame]] | None = None,
        minute_volume_unit: Literal["shares", "lots"] = "shares",
        financials: pd.DataFrame | None = None,
        valuations: pd.DataFrame | None = None,
        benchmark: pd.DataFrame | None = None,
        industry_rps: pd.DataFrame | None = None,
    ) -> SelectionRunResult:
        quality = validate_market_frame(daily, granularity="daily")
        if not quality.valid:
            return SelectionRunResult(
                status="blocked_data_quality",
                selection_date=None,
                freshness=None,
                candidates=[],
                rejected_candidates=[],
                strategy_pools={},
                snapshot_ids=[],
                message=quality.message,
            )
        market = daily.copy()
        market["date"] = pd.to_datetime(market["date"]).dt.normalize()
        latest_date = market["date"].max().date()
        latest_market = market.loc[market["date"].dt.date == latest_date]
        minute_dates = [
            pd.to_datetime(frame["timestamp"]).max().date()
            for frame in (minute_data or {}).values()
            if not frame.empty
        ]
        freshness = self.freshness_gate.evaluate(
            FreshnessInput(
                now=now,
                trading_dates=trading_dates,
                daily_market_max_date=latest_date,
                minute_market_max_date=max(minute_dates) if minute_dates else None,
                daily_symbol_count=int(latest_market["symbol"].nunique()),
                minute_symbol_count=len(minute_data or {}),
                expected_universe_size=expected_universe_size,
            )
        )
        if freshness.selection_status != "ready":
            return SelectionRunResult(
                status=freshness.selection_status,
                selection_date=latest_date,
                freshness=freshness,
                candidates=[],
                rejected_candidates=[],
                strategy_pools={},
                snapshot_ids=[],
                message=freshness.message,
            )

        universe_symbols = self._universe(latest_market)
        market = market.loc[market["symbol"].astype(str).isin(universe_symbols)]
        factors = DailyFactorCalculator().calculate(
            market,
            financials=financials,
            valuations=valuations,
            benchmark=benchmark,
            industry_rps=industry_rps,
        )
        latest_factors = factors.loc[factors["date"].dt.date == latest_date].copy()
        definitions = [item for item in default_factor_definitions() if item.code in latest_factors]
        factor_output = FactorPipeline(
            definitions,
            PipelineConfig(
                normalization="percentile",
                industry_neutral=False,
                market_cap_neutral="market_cap" in latest_factors,
                calculation_version=self.factor_version,
            ),
        ).transform(latest_factors)
        risk_definitions = [
            item for item in default_risk_definitions() if item.code in latest_factors
        ]
        risk_output = (
            FactorPipeline(
                risk_definitions,
                PipelineConfig(
                    normalization="robust_zscore",
                    industry_neutral=False,
                    market_cap_neutral=False,
                    calculation_version=self.factor_version,
                ),
            ).transform(latest_factors)
            if risk_definitions
            else None
        )
        risk_scores = (
            risk_output.scores[["date", "symbol", "risk_contribution", "available_weight"]].assign(
                risk_penalty=lambda frame: (
                    (
                        frame["risk_contribution"]
                        / frame["available_weight"].replace(0, np.nan)
                        * 10.0
                    )
                    .fillna(0.0)
                    .clip(lower=0.0, upper=15.0)
                )
            )[["date", "symbol", "risk_penalty"]]
            if risk_output is not None
            else latest_factors[["date", "symbol"]].assign(risk_penalty=0.0)
        )
        ranked = latest_factors.merge(
            factor_output.scores[["date", "symbol", "composite_score", "data_quality"]],
            on=["date", "symbol"],
            how="left",
        ).merge(risk_scores, on=["date", "symbol"], how="left")
        ranked["risk_penalty"] = ranked["risk_penalty"].fillna(0.0)
        ranked = ranked.sort_values("composite_score", ascending=False)
        ranked = ranked.head(200)
        if minute_loader is not None:
            try:
                loaded_minutes = minute_loader(
                    ranked.head(50)["symbol"].astype(str).tolist(), latest_date
                )
                minute_data = {**(minute_data or {}), **loaded_minutes}
            except Exception:
                # Minute confirmation is deliberately degradable.  Daily data
                # freshness and quality still decide whether selection runs.
                minute_data = minute_data or {}
        factor_details = factor_output.details.copy()
        factor_details["is_risk"] = False
        factor_details["risk_penalty_contribution"] = 0.0
        if risk_output is not None:
            risk_details = risk_output.details.merge(
                risk_output.scores[["date", "symbol", "available_weight"]],
                on=["date", "symbol"],
                how="left",
            )
            risk_details["is_risk"] = True
            risk_details["risk_penalty_contribution"] = (
                risk_details["risk_contribution"]
                / risk_details["available_weight"].replace(0, np.nan)
                * 10.0
            )
            risk_details = risk_details.drop(columns=["available_weight"])
            factor_details = pd.concat(
                [factor_details, risk_details],
                ignore_index=True,
            )

        pa = PriceActionAnalyzer().analyze(
            market.loc[market["symbol"].astype(str).isin(ranked["symbol"])]
        )
        pa_latest = pa.sort_values("date").groupby("symbol").tail(1).set_index("symbol")
        wyckoff = WyckoffCandidateDetector().detect(
            market.loc[market["symbol"].astype(str).isin(ranked["symbol"])]
        )
        scorer = CandidateScorer()
        minute_analyzer = MinuteConfirmationAnalyzer()
        candidates: list[dict[str, Any]] = []
        for row in ranked.head(50).to_dict(orient="records"):
            symbol = str(row["symbol"])
            pa_row = pa_latest.loc[symbol].to_dict() if symbol in pa_latest.index else {}
            merged = {**row, **pa_row}
            merged["factor_details"] = factor_details.loc[
                factor_details["symbol"].astype(str) == symbol
            ].to_dict(orient="records")
            recent_wyckoff = wyckoff.loc[
                (wyckoff["symbol"] == symbol)
                & (
                    pd.to_datetime(wyckoff["signal_date"])
                    >= pd.Timestamp(latest_date) - pd.Timedelta(days=20)
                )
            ]
            wyckoff_score = self._wyckoff_score(recent_wyckoff)
            minute_result = (
                minute_analyzer.analyze_for_date(
                    minute_data[symbol],
                    latest_date,
                    breakout_price=(
                        float(merged["prior_high20"])
                        if pd.notna(merged.get("prior_high20"))
                        else None
                    ),
                    volume_unit=minute_volume_unit,
                )
                if minute_data and symbol in minute_data
                else minute_analyzer.unavailable()
            )
            hard_gates = self._hard_gates(merged)
            candidate_score = scorer.score(
                base_daily_score=float(merged.get("composite_score", 0.0)) * 0.7,
                pa_score=float(merged.get("pa_score", 0.0)),
                wyckoff_score=wyckoff_score,
                minute_score=minute_result.minute_score,
                hard_gate_reasons=hard_gates,
                risk_penalty=float(merged.get("risk_penalty", 0.0)),
            )
            merged["minute_confirmation"] = minute_result.status
            merged["minute_features"] = asdict(minute_result)
            merged["minute_score"] = minute_result.minute_score
            merged["wyckoff_candidates"] = recent_wyckoff.to_dict(orient="records")
            merged["wyckoff_score"] = wyckoff_score
            merged.update(asdict(candidate_score))
            merged["selection_date"] = latest_date.isoformat()
            merged["strategies"] = classify_candidate(merged) if candidate_score.eligible else []
            merged["unavailable_microstructure"] = dict(UNAVAILABLE_MICROSTRUCTURE)
            merged["strategy_version"] = self.strategy_version
            merged["factor_version"] = self.factor_version
            candidates.append(self._json_safe(merged))
        candidates.sort(
            key=lambda item: (
                item["eligible"],
                item["total_score"] if item["total_score"] is not None else -1,
            ),
            reverse=True,
        )
        rejected_candidates = [item for item in candidates if not item["eligible"]]
        candidates = [item for item in candidates if item["eligible"]]
        strategy_pools = {
            code: [item for item in candidates if code in item["strategies"]]
            for code in STRATEGY_LABELS
        }
        snapshot_sources: dict[str, pd.DataFrame | None] = {
            "daily": market,
            "financials": financials,
            "valuations": valuations,
            "benchmark": benchmark,
            "industry_rps": industry_rps,
            "selection_config": pd.DataFrame([{"minute_volume_unit": minute_volume_unit}]),
        }
        snapshot_sources.update(
            {f"minute/{symbol}": frame for symbol, frame in sorted((minute_data or {}).items())}
        )
        snapshot_hash = data_snapshot_version(snapshot_sources)
        snapshot_ids = self._save_snapshots(latest_date, strategy_pools, snapshot_hash)
        return SelectionRunResult(
            status="ready",
            selection_date=latest_date,
            freshness=freshness,
            candidates=candidates,
            rejected_candidates=rejected_candidates,
            strategy_pools=strategy_pools,
            snapshot_ids=snapshot_ids,
            message="正式候选池已生成；系统不自动下单。",
        )

    @staticmethod
    def _universe(latest_market: pd.DataFrame) -> list[str]:
        eligible = latest_market.copy()
        for column in ("is_st", "delisting_risk", "suspended"):
            if column in eligible:
                eligible = eligible.loc[~eligible[column].eq(True)]
        if "listing_days" in eligible:
            # PR 5: listing_days is only meaningful when derived from a real
            # security master.  All-NaN means no real listing dates were
            # available (importer writes NaN) -> skip the new-listing filter
            # rather than dropping the whole universe via fillna(0).
            listing_days = pd.to_numeric(eligible["listing_days"], errors="coerce")
            if listing_days.notna().any():
                eligible = eligible.loc[listing_days.fillna(0) >= 60]
        eligible = eligible.loc[eligible["volume"].fillna(0) > 0]
        return eligible["symbol"].astype(str).tolist()

    @staticmethod
    def _wyckoff_score(signals: pd.DataFrame) -> float:
        score = 5.0
        supportive = {
            "spring_candidate",
            "sign_of_strength_candidate",
            "last_point_of_support_candidate",
        }
        contradictory = {"upthrust_candidate", "sign_of_weakness_candidate"}
        score += 1.5 * signals["signal_type"].isin(supportive).sum()
        score -= 2.0 * signals["signal_type"].isin(contradictory).sum()
        return float(min(max(score, 0.0), 10.0))

    @staticmethod
    def _hard_gates(row: dict[str, Any]) -> list[str]:
        reasons = []
        if float(row.get("data_quality_risk", 1.0) or 0.0) > 0.3:
            reasons.append("data_quality_failed")
        if float(row.get("one_word_limit_consecutive", 0.0) or 0.0) >= 2:
            reasons.append("consecutive_one_word_untradable")
        if float(row.get("failed_breakout", 0.0) or 0.0) > 0:
            reasons.append("confirmed_failed_breakout")
        if float(row.get("high_volume_stall", 0.0) or 0.0) > 0:
            reasons.append("high_volume_stall")
        if bool(row.get("delisting_risk", False)):
            reasons.append("major_delisting_risk")
        if bool(row.get("severe_financial_anomaly", False)):
            reasons.append("severe_financial_anomaly")
        return reasons

    def _save_snapshots(
        self,
        selection_date: date,
        pools: dict[str, list[dict[str, Any]]],
        snapshot_hash: str,
    ) -> list[int]:
        ids: list[int] = []
        if self.snapshot_repository is None:
            return ids
        for strategy_code, candidates in pools.items():
            artifact = write_candidate_artifact(
                candidates,
                artifact_root=self.artifact_root,
                selection_date=selection_date,
                strategy_code=strategy_code,
                strategy_version=self.strategy_version,
                factor_version=self.factor_version,
                snapshot_version=snapshot_hash,
            )
            record = self.snapshot_repository.create(
                selection_date=selection_date,
                strategy_code=strategy_code,
                strategy_version=self.strategy_version,
                factor_version=self.factor_version,
                data_snapshot_version=snapshot_hash,
                selection_status="ready",
                candidates=candidates,
                artifact_path=str(artifact),
            )
            ids.append(record.id)
        return ids

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: DailySelectionPipeline._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [DailySelectionPipeline._json_safe(item) for item in value]
        if isinstance(value, (pd.Timestamp, datetime, date)):
            return value.isoformat()
        if isinstance(value, np.bool_):
            return bool(value)
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, (np.floating, float)):
            return float(value) if np.isfinite(value) else None
        return value
