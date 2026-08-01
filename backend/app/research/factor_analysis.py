from __future__ import annotations

import numpy as np
import pandas as pd

from app.adapters.io import filter_date_window, read_tabular
from app.domain.protocols import FactorAnalysisRequest, FactorAnalysisResult


class NativeFactorAnalysisEngine:
    """Reference implementation used for cross-checking external factor engines."""

    engine_code = "native"

    def analyze(self, request: FactorAnalysisRequest) -> FactorAnalysisResult:
        if request.factor_path is None or request.price_path is None:
            raise ValueError("native factor analysis requires factor_path and price_path")
        return self.analyze_frames(
            request,
            read_tabular(request.factor_path),
            read_tabular(request.price_path),
        )

    def analyze_frames(
        self,
        request: FactorAnalysisRequest,
        factors: pd.DataFrame,
        prices: pd.DataFrame,
    ) -> FactorAnalysisResult:
        required_factor = {"date", "symbol", request.factor_code}
        required_price = {"date", "symbol", "close"}
        if missing := required_factor.difference(factors.columns):
            raise ValueError(f"factor input is missing columns: {sorted(missing)}")
        if missing := required_price.difference(prices.columns):
            raise ValueError(f"price input is missing columns: {sorted(missing)}")
        factor_frame = factors.copy()
        factor_frame["date"] = pd.to_datetime(factor_frame["date"]).dt.normalize()
        factor_frame = filter_date_window(
            factor_frame,
            column="date",
            start=request.start_date,
            end=request.end_date,
        )
        price_frame = prices.copy()
        price_frame["date"] = pd.to_datetime(price_frame["date"]).dt.normalize()
        price_frame = price_frame.loc[
            price_frame["date"] >= pd.Timestamp(request.start_date)
        ].copy()
        price_matrix = price_frame.pivot(index="date", columns="symbol", values="close")
        values = factor_frame.pivot(
            index="date", columns="symbol", values=request.factor_code
        ).astype(float)
        industries = (
            factor_frame.pivot(index="date", columns="symbol", values="industry")
            if "industry" in factor_frame
            else None
        )
        ic: dict[int, float] = {}
        rank_ic: dict[int, float] = {}
        ic_std: dict[int, float] = {}
        icir: dict[int, float] = {}
        quantile_returns: dict[int, list[float]] = {}
        long_short: dict[int, float] = {}
        turnover: dict[int, float] = {}
        industry_results: dict[str, dict[str, list[float]]] = {}
        total_possible = int(values.notna().sum().sum())
        used = 0
        for horizon in request.horizons:
            forward = price_matrix.shift(-horizon) / price_matrix - 1.0
            common_dates = values.index.intersection(forward.index)
            pearson_series: list[float] = []
            spearman_series: list[float] = []
            quantile_daily: list[pd.Series] = []
            previous_quantile: pd.Series | None = None
            turnover_values: list[float] = []
            for trade_date in common_dates:
                section = pd.DataFrame(
                    {
                        "factor": values.loc[trade_date],
                        "forward": forward.loc[trade_date],
                    }
                ).dropna()
                if request.industry_neutral:
                    if industries is None or trade_date not in industries.index:
                        raise ValueError("industry_neutral analysis requires historical industry")
                    section["industry"] = industries.loc[trade_date].reindex(section.index)
                    section = section.dropna(subset=["industry"])
                    section["forward"] = section["forward"] - section.groupby(
                        "industry", observed=True
                    )["forward"].transform("mean")
                if len(section) < 2:
                    continue
                used += len(section)
                pearson_series.append(float(section["factor"].corr(section["forward"])))
                spearman_series.append(
                    float(section["factor"].corr(section["forward"], method="spearman"))
                )
                unique = section["factor"].nunique()
                groups = min(request.group_count, unique, len(section))
                if groups >= 2:
                    section["quantile"] = (
                        pd.qcut(
                            section["factor"].rank(method="first"),
                            q=groups,
                            labels=False,
                        )
                        + 1
                    )
                    quantile_daily.append(section.groupby("quantile")["forward"].mean())
                    current_quantile = section["quantile"]
                    if previous_quantile is not None:
                        common = current_quantile.index.intersection(previous_quantile.index)
                        if len(common):
                            turnover_values.append(
                                float(
                                    (
                                        current_quantile.loc[common]
                                        != previous_quantile.loc[common]
                                    ).mean()
                                )
                            )
                    previous_quantile = current_quantile
                if industries is not None and trade_date in industries.index:
                    if "industry" not in section:
                        section["industry"] = industries.loc[trade_date].reindex(section.index)
                    for industry, group in section.groupby("industry"):
                        if len(group) >= 2:
                            industry_metrics = industry_results.setdefault(str(industry), {})
                            industry_values = industry_metrics.setdefault(f"{horizon}D_values", [])
                            industry_values.append(
                                float(group["factor"].corr(group["forward"], method="spearman"))
                            )
            pearson = pd.Series(pearson_series, dtype=float)
            spearman = pd.Series(spearman_series, dtype=float)
            mean_ic = self._finite(float(pearson.mean()))
            std_ic = self._finite(float(pearson.std(ddof=0)))
            ic[horizon] = mean_ic
            rank_ic[horizon] = self._finite(float(spearman.mean()))
            ic_std[horizon] = std_ic
            icir[horizon] = self._finite(mean_ic / std_ic if std_ic > 0 else 0.0)
            if quantile_daily:
                means = pd.concat(quantile_daily, axis=1).mean(axis=1).sort_index()
                quantile_returns[horizon] = [self._finite(float(item)) for item in means]
            else:
                quantile_returns[horizon] = []
            long_short[horizon] = (
                quantile_returns[horizon][-1] - quantile_returns[horizon][0]
                if len(quantile_returns[horizon]) >= 2
                else 0.0
            )
            turnover[horizon] = (
                self._finite(float(np.mean(turnover_values))) if turnover_values else 0.0
            )
        clean_industry: dict[str, dict[str, float]] = {}
        for industry, metrics in industry_results.items():
            clean_industry[industry] = {
                key.replace("_values", ""): self._finite(float(np.mean(value)))
                for key, value in metrics.items()
            }
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
            coverage=min(used / max(total_possible * len(request.horizons), 1), 1.0),
            industry_results=clean_industry,
            metadata={
                "analysis_engine": self.engine_code,
                "forward_returns_used_only_as_labels": True,
                "factor_date_count": int(factor_frame["date"].nunique()),
                "industry_neutral": request.industry_neutral,
            },
        )

    @staticmethod
    def _finite(value: float) -> float:
        return value if np.isfinite(value) else 0.0
