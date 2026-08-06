"""Causal replay of the production trend-quality selection rule.

The daily selection endpoint intentionally works on one latest date.  Walk-forward
research needs the same rule evaluated for every historical date while preserving the
after-close information boundary.  This module is the public seam for that replay;
it does not calculate forward returns and it never emits a production promotion.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, time
from typing import Any

import numpy as np
import pandas as pd

from app.data.contracts import DAILY_REQUIRED_COLUMNS, POINT_IN_TIME_REQUIRED_COLUMNS
from app.data.history import join_historical_state
from app.research.factors.calculator import DailyFactorCalculator
from app.research.factors.catalog import default_factor_definitions, default_risk_definitions
from app.research.factors.pipeline import FactorPipeline, PipelineConfig
from app.research.pa import PriceActionAnalyzer
from app.research.scoring import CandidateScorer
from app.research.wyckoff import WYCKOFF_COLUMNS, WyckoffCandidateDetector
from app.selection.pipeline import DailySelectionPipeline
from app.selection.snapshots import data_snapshot_version as compute_snapshot_version
from app.selection.strategies import classify_candidate


@dataclass(frozen=True, slots=True)
class HistoricalSignalResult:
    """Versioned historical signals and the rows rejected by hard gates."""

    signals: pd.DataFrame
    rejected: pd.DataFrame
    data_snapshot_version: str
    strategy_code: str
    strategy_version: str
    factor_version: str
    point_in_time_cutoff: str
    manifest: dict[str, Any]

    @property
    def audit(self) -> dict[str, Any]:
        """Compatibility alias for research services consuming generator audits."""

        return self.manifest


class HistoricalSignalGenerator:
    """Replay ``trend_quality_v1`` with only data known at each signal date.

    The factor calculator, PA detector, Wyckoff detector, scorer, and strategy
    classifier are the same implementations used by ``DailySelectionPipeline``.
    Forward labels are deliberately outside this class.  Missing minute data is
    represented as unavailable and never as a zero score.
    """

    strategy_code = "trend_quality_v1"
    strategy_version = DailySelectionPipeline.strategy_version
    factor_version = DailySelectionPipeline.factor_version
    information_cutoff = time(18, 30)
    # FactorPipeline materialises one audit row per factor and security.  Keep
    # the cross-sectional work bounded while preserving exactly the same
    # per-date normalisation and ranking semantics.
    cross_section_batch_days = 5

    def generate(
        self,
        *,
        daily: pd.DataFrame,
        trading_dates: Sequence[date] | None = None,
        financials: pd.DataFrame | None = None,
        valuations: pd.DataFrame | None = None,
        benchmark: pd.DataFrame | None = None,
        industry_rps: pd.DataFrame | None = None,
        state_history: pd.DataFrame | None = None,
        security_master: pd.DataFrame | None = None,
        data_snapshot_version: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        max_candidates_per_date: int = 50,
    ) -> HistoricalSignalResult:
        """Generate one signal row per eligible trend candidate and date.

        ``state_history`` may contain effective-date rows for historical industry,
        ST, suspension, listing, and delisting state.  Financial and valuation
        inputs must include the audit columns required by ``available_at`` joins.
        ``trading_dates`` is used only to reject bars outside the supplied calendar;
        callers may omit it for a prevalidated snapshot.
        """

        if max_candidates_per_date < 1:
            raise ValueError("max_candidates_per_date must be positive")
        market = self._prepare_market(daily, state_history=state_history)
        if security_master is not None and not security_master.empty:
            # PR 5.1: real listing dates from the security master override
            # any importer-derived value; missing list_date stays NaN and
            # the new-listing filter rejects those symbols.
            from app.data.security_master import listing_days_for

            listing_days, _ = listing_days_for(market, security_master)
            market = market.copy()
            market["listing_days"] = listing_days.to_numpy()
        self._validate_point_in_time_dataset(financials, "financials")
        self._validate_point_in_time_dataset(valuations, "valuations")
        if trading_dates is not None:
            calendar_dates = {pd.Timestamp(item).date() for item in trading_dates}
            observed_dates = set(market["date"].dt.date)
            outside = sorted(observed_dates.difference(calendar_dates))
            if outside:
                raise ValueError(
                    "daily data contains dates outside the supplied trading calendar: "
                    f"{outside[:3]}"
                )
        output_start = pd.Timestamp(start_date) if start_date is not None else market["date"].min()
        output_end = pd.Timestamp(end_date) if end_date is not None else market["date"].max()
        if output_start > output_end:
            raise ValueError("historical signal date window is reversed")
        if not ((market["date"] >= output_start) & (market["date"] <= output_end)).any():
            raise ValueError("historical signal input has no rows in the requested date window")

        snapshot_version = data_snapshot_version or compute_snapshot_version(
            {
                "daily": market,
                "financials": financials,
                "valuations": valuations,
                "benchmark": benchmark,
                "industry_rps": industry_rps,
                "state_history": state_history,
            }
        )
        output_mask = market["date"].between(output_start, output_end)
        universe_columns = [
            column
            for column in (
                "date",
                "symbol",
                "volume",
                "is_st",
                "delisting_risk",
                "suspended",
                "listing_days",
            )
            if column in market
        ]
        output_universe = market.loc[output_mask, universe_columns].copy()
        universe_groups = output_universe.groupby("date", sort=False)

        structure_columns = [
            column
            for column in ("date", "symbol", "open", "high", "low", "close", "volume")
            if column in market
        ]
        structure_market = market.loc[:, structure_columns]
        pa = PriceActionAnalyzer().analyze(structure_market)
        pa = pa.loc[pa["date"].between(output_start, output_end)].copy()
        wyckoff = WyckoffCandidateDetector().detect(structure_market)
        wyckoff_lookup = self._wyckoff_lookup(wyckoff)

        factor_market = market.drop(
            columns=[
                "name",
                "pre_close",
                "change",
                "pct_chg",
                "adj_factor",
                "first_adj",
                "adj_open",
                "adj_high",
                "adj_low",
                "adj_close",
                "adj_pre_close",
                "execution_open",
                "execution_high",
                "execution_low",
                "execution_close",
                "execution_pre_close",
                "limit_source",
                "has_minute_data",
            ],
            errors="ignore",
        )
        factors = DailyFactorCalculator().calculate(
            factor_market,
            financials=financials,
            valuations=valuations,
            benchmark=benchmark,
            industry_rps=industry_rps,
            output_start=output_start.date(),
            output_end=output_end.date(),
        )
        definitions = [item for item in default_factor_definitions() if item.code in factors]
        factor_pipeline = FactorPipeline(
            definitions,
            PipelineConfig(
                normalization="percentile",
                industry_neutral=False,
                market_cap_neutral="market_cap" in factors,
                calculation_version=self.factor_version,
            ),
        )
        risk_definitions = [item for item in default_risk_definitions() if item.code in factors]
        risk_pipeline = (
            FactorPipeline(
                risk_definitions,
                PipelineConfig(
                    normalization="robust_zscore",
                    industry_neutral=False,
                    market_cap_neutral=False,
                    calculation_version=self.factor_version,
                ),
            )
            if risk_definitions
            else None
        )

        signals: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        output_dates = [
            pd.Timestamp(value).normalize() for value in sorted(factors["date"].unique())
        ]
        for batch_start in range(0, len(output_dates), self.cross_section_batch_days):
            batch_dates = output_dates[
                batch_start : batch_start + self.cross_section_batch_days
            ]
            batch_factors = factors.loc[factors["date"].isin(batch_dates)].copy()
            factor_output = factor_pipeline.transform(batch_factors)
            risk_output = risk_pipeline.transform(batch_factors) if risk_pipeline else None
            ranked = self._ranked_factors(batch_factors, factor_output, risk_output)

            ranked_sections: dict[pd.Timestamp, pd.DataFrame] = {}
            candidate_keys: list[pd.DataFrame] = []
            for date_value in batch_dates:
                section = ranked.loc[ranked["date"] == date_value].copy()
                try:
                    universe = universe_groups.get_group(date_value)
                except KeyError:
                    continue
                eligible_symbols = DailySelectionPipeline._universe(universe)
                section = section.loc[section["symbol"].astype(str).isin(eligible_symbols)]
                section = section.sort_values("composite_score", ascending=False).head(200)
                ranked_sections[date_value] = section
                candidate_keys.append(section[["date", "symbol"]])
            if not candidate_keys:
                continue

            keys = pd.concat(candidate_keys, ignore_index=True).drop_duplicates()
            factor_details = self._factor_details(factor_output, risk_output).merge(
                keys,
                on=["date", "symbol"],
                how="inner",
            )
            factor_audit_lookup = self._factor_audit_lookup(factor_details)
            pa_lookup = self._pa_lookup(pa.loc[pa["date"].isin(batch_dates)])

            for date_value, section in ranked_sections.items():
                for row in section.to_dict(orient="records")[:max_candidates_per_date]:
                    symbol = str(row["symbol"])
                    pa_row = self._pa_row(pa_lookup, date_value, symbol)
                    merged: dict[str, Any] = {
                        **{str(key): value for key, value in row.items()},
                        **pa_row,
                    }
                    detail = factor_audit_lookup.get((date_value, symbol), [])
                    merged["factor_audit"] = detail
                    recent_wyckoff = self._recent_wyckoff(wyckoff_lookup, symbol, date_value)
                    merged["wyckoff_candidates"] = recent_wyckoff.to_dict(orient="records")
                    merged["wyckoff_score"] = DailySelectionPipeline._wyckoff_score(
                        recent_wyckoff
                    )
                    hard_gates = DailySelectionPipeline._hard_gates(merged)
                    candidate_score = CandidateScorer().score(
                        base_daily_score=float(merged.get("composite_score", 0.0)) * 0.7,
                        pa_score=float(merged.get("pa_score", 0.0) or 0.0),
                        wyckoff_score=float(merged["wyckoff_score"]),
                        minute_score=None,
                        hard_gate_reasons=hard_gates,
                        risk_penalty=float(merged.get("risk_penalty", 0.0) or 0.0),
                    )
                    merged.update(asdict(candidate_score))
                    merged["selection_date"] = date_value.date().isoformat()
                    merged["signal_date"] = date_value
                    merged["strategy_version"] = self.strategy_version
                    merged["factor_version"] = self.factor_version
                    merged["data_snapshot_version"] = snapshot_version
                    merged["strategy_code"] = self.strategy_code
                    merged["score"] = candidate_score.total_score
                    merged["minute_score"] = None
                    merged["minute_confirmation"] = "unavailable"
                    merged["data_confidence"] = candidate_score.data_confidence
                    merged["hard_gate_reasons"] = hard_gates
                    merged["strategies"] = (
                        classify_candidate(merged) if candidate_score.eligible else []
                    )
                    merged["point_in_time_cutoff"] = self.information_cutoff.isoformat()
                    safe_value = self._json_safe(merged)
                    safe: dict[str, Any] = safe_value if isinstance(safe_value, dict) else {}
                    if candidate_score.eligible and self.strategy_code in safe["strategies"]:
                        signals.append(safe)
                    elif hard_gates:
                        rejected.append(safe)

        output_columns = self._output_columns()
        signal_frame = self._frame(signals, output_columns)
        rejected_frame = self._frame(rejected, output_columns)
        if not signal_frame.empty:
            signal_frame = signal_frame.sort_values(["signal_date", "symbol"]).reset_index(
                drop=True
            )
        if not rejected_frame.empty:
            rejected_frame = rejected_frame.sort_values(["signal_date", "symbol"]).reset_index(
                drop=True
            )
        manifest = {
            "snapshot_version": snapshot_version,
            "strategy_code": self.strategy_code,
            "strategy_version": self.strategy_version,
            "factor_version": self.factor_version,
            "point_in_time_cutoff": self.information_cutoff.isoformat(),
            "daily_min_date": market["date"].min().date().isoformat(),
            "daily_max_date": market["date"].max().date().isoformat(),
            "daily_row_count": len(market),
            "daily_symbol_count": int(market["symbol"].nunique()),
            "signal_row_count": len(signal_frame),
            "rejected_row_count": len(rejected_frame),
            "production_enabled": False,
        }
        return HistoricalSignalResult(
            signals=signal_frame,
            rejected=rejected_frame,
            data_snapshot_version=snapshot_version,
            strategy_code=self.strategy_code,
            strategy_version=self.strategy_version,
            factor_version=self.factor_version,
            point_in_time_cutoff=self.information_cutoff.isoformat(),
            manifest=manifest,
        )

    @staticmethod
    def _prepare_market(
        daily: pd.DataFrame,
        *,
        state_history: pd.DataFrame | None,
    ) -> pd.DataFrame:
        missing = DAILY_REQUIRED_COLUMNS.difference(daily.columns)
        if missing:
            raise ValueError(f"daily market data is missing columns: {sorted(missing)}")
        market = daily.copy()
        market["date"] = pd.to_datetime(
            market["date"], format="mixed", errors="raise"
        ).dt.normalize()
        market["symbol"] = market["symbol"].astype(str)
        if market.duplicated(["date", "symbol"]).any():
            raise ValueError("historical daily input contains duplicate date/symbol rows")
        # The purchased archive keeps raw prices for execution and causal
        # adjusted prices for research.  Never overwrite the execution frame
        # outside this generator; the formal engine receives the raw columns.
        adjusted = {
            column: f"adj_{column}"
            for column in ("open", "high", "low", "close", "pre_close")
            if column in market.columns and f"adj_{column}" in market.columns
        }
        core_adjusted = {"open", "high", "low", "close"}.issubset(adjusted)
        if core_adjusted:
            for column, adjusted_column in adjusted.items():
                market[f"execution_{column}"] = market[column]
                market[column] = pd.to_numeric(market[adjusted_column], errors="coerce")
            market["price_basis"] = "causal_hfq"
        else:
            market["price_basis"] = "raw"
        if state_history is not None and not state_history.empty:
            market = join_historical_state(market, state_history)
        if "list_date" in market:
            list_dates = pd.to_datetime(market["list_date"], format="mixed", errors="coerce")
            market["listing_days"] = (market["date"] - list_dates).dt.days.astype(float)
        market.sort_values(["date", "symbol"], inplace=True)
        market.index = pd.RangeIndex(len(market))
        return market

    @staticmethod
    def _validate_point_in_time_dataset(
        frame: pd.DataFrame | None,
        name: str,
    ) -> None:
        if frame is None or frame.empty:
            return
        missing = POINT_IN_TIME_REQUIRED_COLUMNS.difference(frame.columns)
        if missing:
            raise ValueError(f"{name} is missing point-in-time audit columns: {sorted(missing)}")
        available = pd.to_datetime(frame["available_at"], format="mixed", errors="raise")
        if available.isna().any():
            raise ValueError(f"{name}.available_at contains invalid timestamps")

    @staticmethod
    def _ranked_factors(
        factors: pd.DataFrame,
        factor_output: Any,
        risk_output: Any,
    ) -> pd.DataFrame:
        if risk_output is not None:
            risk_scores = risk_output.scores[
                ["date", "symbol", "risk_contribution", "available_weight"]
            ].copy()
            risk_scores["risk_penalty"] = (
                (
                    risk_scores["risk_contribution"]
                    / risk_scores["available_weight"].replace(0, np.nan)
                    * 10.0
                )
                .fillna(0.0)
                .clip(lower=0.0, upper=15.0)
            )
            risk_scores = risk_scores[["date", "symbol", "risk_penalty"]]
        else:
            risk_scores = factors[["date", "symbol"]].assign(risk_penalty=0.0)
        ranked = factors.merge(
            factor_output.scores[["date", "symbol", "composite_score", "data_quality"]],
            on=["date", "symbol"],
            how="left",
        ).merge(risk_scores, on=["date", "symbol"], how="left")
        ranked["risk_penalty"] = ranked["risk_penalty"].fillna(0.0)
        return ranked.sort_values(["date", "composite_score"], ascending=[True, False])

    @staticmethod
    def _factor_details(factor_output: Any, risk_output: Any) -> pd.DataFrame:
        details: pd.DataFrame = pd.DataFrame(factor_output.details.copy())
        details["is_risk"] = False
        details["risk_penalty_contribution"] = 0.0
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
            details = pd.concat(
                [details, risk_details.drop(columns=["available_weight"])],
                ignore_index=True,
            )
        return details

    @staticmethod
    def _factor_audit_lookup(
        details: pd.DataFrame,
    ) -> dict[tuple[pd.Timestamp, str], list[dict[str, Any]]]:
        lookup: dict[tuple[pd.Timestamp, str], list[dict[str, Any]]] = {}
        for (item_date, symbol), group in details.groupby(["date", "symbol"], sort=False):
            key = (pd.Timestamp(str(item_date)).normalize(), str(symbol))
            lookup[key] = [
                {str(column): value for column, value in row.items()}
                for row in group.to_dict(orient="records")
            ]
        return lookup

    @staticmethod
    def _pa_lookup(pa: pd.DataFrame) -> dict[tuple[pd.Timestamp, str], dict[str, Any]]:
        if pa.empty:
            return {}
        lookup: dict[tuple[pd.Timestamp, str], dict[str, Any]] = {}
        for row in pa.to_dict(orient="records"):
            key = (pd.Timestamp(row["date"]).normalize(), str(row["symbol"]))
            lookup[key] = {str(column): value for column, value in row.items()}
        return lookup

    @staticmethod
    def _pa_row(
        pa_lookup: dict[tuple[pd.Timestamp, str], dict[str, Any]],
        signal_date: pd.Timestamp,
        symbol: str,
    ) -> dict[str, Any]:
        return pa_lookup.get((signal_date.normalize(), symbol), {})

    @staticmethod
    def _wyckoff_lookup(wyckoff: pd.DataFrame) -> dict[str, pd.DataFrame]:
        if wyckoff.empty:
            return {}
        return {
            str(symbol): group.copy() for symbol, group in wyckoff.groupby("symbol", sort=False)
        }

    @staticmethod
    def _recent_wyckoff(
        wyckoff: dict[str, pd.DataFrame],
        symbol: str,
        signal_date: pd.Timestamp,
    ) -> pd.DataFrame:
        symbol_frame = wyckoff.get(symbol)
        if symbol_frame is None or symbol_frame.empty:
            return pd.DataFrame(columns=WYCKOFF_COLUMNS)
        dates = pd.to_datetime(symbol_frame["signal_date"], format="mixed", errors="coerce")
        result = symbol_frame.loc[
            (dates <= signal_date) & (dates >= signal_date - pd.Timedelta(days=20))
        ].copy()
        return pd.DataFrame(result)

    @staticmethod
    def _output_columns() -> list[str]:
        return [
            "signal_date",
            "symbol",
            "industry",
            "score",
            "composite_score",
            "return_20d",
            "rps_60d",
            "rps_120d",
            "strategy_code",
            "strategy_version",
            "factor_version",
            "data_snapshot_version",
            "price_basis",
            "factor_audit",
            "hard_gate_reasons",
            "data_confidence",
            "minute_confirmation",
            "minute_score",
            "eligible",
            "total_score",
            "base_daily_score",
            "pa_score",
            "wyckoff_score",
            "risk_penalty",
            "data_quality_risk",
            "is_st",
            "delisting_risk",
            "suspended",
            "strategies",
            "point_in_time_cutoff",
        ]

    @staticmethod
    def _frame(rows: list[dict[str, Any]], columns: list[str]) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame(columns=columns)
        frame = pd.DataFrame(rows)
        for column in columns:
            if column not in frame:
                frame[column] = None
        return frame[columns]

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        if isinstance(value, (pd.Timestamp, pd.Timedelta)):
            return value.isoformat()
        if isinstance(value, (date,)):
            return value.isoformat()
        if isinstance(value, np.bool_):
            return bool(value)
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, (np.floating, float)):
            return float(value) if np.isfinite(value) else None
        if isinstance(value, (pd.NaT.__class__,)):
            return None
        return value
