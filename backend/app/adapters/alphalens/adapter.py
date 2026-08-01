from __future__ import annotations

from importlib.util import find_spec
from typing import Any

import numpy as np
import pandas as pd

from app.adapters import OptionalEngineUnavailableError
from app.adapters.alphalens.converter import prepare_alphalens_input
from app.adapters.io import filter_date_window, read_tabular
from app.domain.protocols import FactorAnalysisRequest, FactorAnalysisResult


class AlphalensFactorAnalysisAdapter:
    """Stable system interface around Alphalens Reloaded."""

    engine_code = "alphalens"

    @staticmethod
    def is_available() -> bool:
        return find_spec("alphalens") is not None

    def analyze(self, request: FactorAnalysisRequest) -> FactorAnalysisResult:
        if request.factor_path is None or request.price_path is None:
            raise ValueError("Alphalens analysis requires factor_path and price_path")
        return self.analyze_frames(
            request,
            read_tabular(request.factor_path),
            read_tabular(request.price_path),
        )

    def analyze_frames(
        self,
        request: FactorAnalysisRequest,
        factor_frame: pd.DataFrame,
        price_frame: pd.DataFrame,
    ) -> FactorAnalysisResult:
        if not self.is_available():
            raise OptionalEngineUnavailableError(
                "Alphalens Reloaded 未安装；运行 uv sync --extra factor-research。"
            )
        # Third-party import is intentionally confined to this adapter.
        from alphalens import performance, utils

        factor_frame = filter_date_window(
            factor_frame,
            column="date",
            start=request.start_date,
            end=request.end_date,
        )
        prices = price_frame.copy()
        prices["date"] = pd.to_datetime(prices["date"]).dt.tz_localize(None).dt.normalize()
        price_frame = prices.loc[prices["date"] >= pd.Timestamp(request.start_date)].copy()
        converted = prepare_alphalens_input(factor_frame, price_frame, request.factor_code)
        clean_kwargs: dict[str, Any] = {
            "factor": converted.factor,
            "prices": converted.prices,
            "periods": tuple(request.horizons),
            "groupby": converted.groups,
            "max_loss": 1.0,
        }
        if converted.is_discrete:
            clean_kwargs.update(quantiles=None, bins=min(2, request.group_count))
        else:
            clean_kwargs.update(quantiles=request.group_count, bins=None)
        clean = utils.get_clean_factor_and_forward_returns(**clean_kwargs)
        if clean.empty:
            raise ValueError("Alphalens produced no clean factor observations")

        rank_ic_frame = performance.factor_information_coefficient(
            clean,
            group_adjust=request.industry_neutral,
            by_group=False,
        )
        mean_by_quantile, _ = performance.mean_return_by_quantile(
            clean,
            by_date=False,
            demeaned=False,
            group_adjust=request.industry_neutral,
        )
        industry_results: dict[str, Any] = {}
        if converted.groups is not None:
            grouped_ic = performance.factor_information_coefficient(
                clean,
                group_adjust=request.industry_neutral,
                by_group=True,
            )
            if not grouped_ic.empty:
                for group, values in grouped_ic.groupby(level="group", observed=True):
                    industry_results[str(group)] = {
                        str(column): self._finite(values[column].mean())
                        for column in values.columns
                    }

        ic: dict[int, float] = {}
        rank_ic: dict[int, float] = {}
        ic_std: dict[int, float] = {}
        icir: dict[int, float] = {}
        quantile_returns: dict[int, list[float]] = {}
        long_short: dict[int, float] = {}
        turnover: dict[int, float] = {}
        for horizon in request.horizons:
            column = self._period_column(clean, horizon)
            pearson_by_date = clean.groupby(level="date").apply(
                lambda section, period_column=column: self._correlation(
                    section["factor"], section[period_column]
                )
            )
            pearson_mean = float(pearson_by_date.mean())
            pearson_std = float(pearson_by_date.std(ddof=0))
            ic[horizon] = self._finite(pearson_mean)
            rank_ic[horizon] = self._finite(float(rank_ic_frame[column].mean()))
            ic_std[horizon] = self._finite(pearson_std)
            icir[horizon] = self._finite(pearson_mean / pearson_std if pearson_std > 0 else 0.0)
            quantile_values = (
                mean_by_quantile[column].groupby(level="factor_quantile").mean().sort_index()
            )
            quantile_returns[horizon] = [
                self._finite(float(item)) for item in quantile_values.tolist()
            ]
            long_short[horizon] = self._finite(
                float(quantile_values.iloc[-1] - quantile_values.iloc[0])
                if len(quantile_values) >= 2
                else 0.0
            )
            quantile_turnovers = []
            for quantile in sorted(clean["factor_quantile"].dropna().unique()):
                series = performance.quantile_turnover(
                    clean["factor_quantile"],
                    quantile=int(quantile),
                    period=horizon,
                )
                quantile_turnovers.append(float(series.mean()))
            turnover[horizon] = self._finite(
                float(np.nanmean(quantile_turnovers)) if quantile_turnovers else 0.0
            )

        return FactorAnalysisResult(
            factor_code=request.factor_code,
            start_date=request.start_date,
            end_date=request.end_date,
            ic=ic,
            rank_ic=rank_ic,
            ic_std=ic_std,
            icir=icir,
            quantile_returns=quantile_returns,
            long_short_returns=long_short,
            turnover=turnover,
            coverage=len(clean) / max(converted.source_rows, 1),
            industry_results=industry_results,
            metadata={
                "analysis_engine": self.engine_code,
                "library": "alphalens-reloaded",
                "industry_neutral": request.industry_neutral,
                "discrete_factor": converted.is_discrete,
                "horizons": request.horizons,
                "factor_date_count": int(converted.factor.index.get_level_values("date").nunique()),
            },
        )

    @staticmethod
    def _period_column(frame: pd.DataFrame, horizon: int) -> str:
        candidates = [f"{horizon}D", str(horizon)]
        for candidate in candidates:
            if candidate in frame.columns:
                return candidate
        timedelta = pd.Timedelta(days=horizon)
        for column in frame.columns:
            try:
                if pd.Timedelta(column) == timedelta:
                    return str(column)
            except (TypeError, ValueError):
                continue
        raise ValueError(f"Alphalens result is missing {horizon}-day forward returns")

    @staticmethod
    def _correlation(left: pd.Series, right: pd.Series) -> float:
        valid = left.notna() & right.notna()
        if valid.sum() < 2:
            return float("nan")
        return float(left.loc[valid].corr(right.loc[valid], method="pearson"))

    @staticmethod
    def _finite(value: float) -> float:
        return value if np.isfinite(value) else 0.0
