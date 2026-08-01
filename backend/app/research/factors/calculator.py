from __future__ import annotations

from datetime import time

import numpy as np
import pandas as pd

from app.data.contracts import POINT_IN_TIME_REQUIRED_COLUMNS


def asof_join_available_data(
    daily: pd.DataFrame,
    point_in_time: pd.DataFrame,
    *,
    information_cutoff: time = time(18, 30),
) -> pd.DataFrame:
    """Join records known by the reproducible after-close selection cutoff."""
    required = {"symbol", "available_at"}
    if missing := required.difference(point_in_time.columns):
        raise ValueError(f"point-in-time data is missing columns: {sorted(missing)}")
    if metadata_missing := POINT_IN_TIME_REQUIRED_COLUMNS.difference(point_in_time.columns):
        raise ValueError(
            f"point-in-time data is missing audit metadata: {sorted(metadata_missing)}"
        )
    left = daily.copy()
    left["date"] = pd.to_datetime(left["date"]).dt.normalize()
    left["symbol"] = left["symbol"].astype(str)
    left["_asof_timestamp"] = left["date"] + pd.Timedelta(
        hours=information_cutoff.hour,
        minutes=information_cutoff.minute,
        seconds=information_cutoff.second,
    )
    right = point_in_time.copy()
    right["symbol"] = right["symbol"].astype(str)
    right["available_at"] = pd.to_datetime(
        right["available_at"],
        format="mixed",
        errors="raise",
    )

    joined: list[pd.DataFrame] = []
    for symbol, left_symbol in left.groupby("symbol", sort=False):
        right_symbol = right.loc[right["symbol"] == symbol].sort_values("available_at")
        left_symbol = left_symbol.sort_values("_asof_timestamp")
        if right_symbol.empty:
            joined.append(left_symbol)
            continue
        right_symbol = right_symbol.drop(columns=["symbol"])
        joined.append(
            pd.merge_asof(
                left_symbol,
                right_symbol,
                left_on="_asof_timestamp",
                right_on="available_at",
                direction="backward",
                allow_exact_matches=True,
                suffixes=("", "_reported"),
            )
        )
    output = pd.concat(joined, ignore_index=True).drop(columns=["_asof_timestamp"])
    return output.sort_values(["date", "symbol"]).reset_index(drop=True)


class DailyFactorCalculator:
    """Transparent daily factors; no ML model participates in formal ranking."""

    def calculate(
        self,
        daily: pd.DataFrame,
        *,
        financials: pd.DataFrame | None = None,
        valuations: pd.DataFrame | None = None,
        benchmark: pd.DataFrame | None = None,
        industry_rps: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        required = {
            "date",
            "symbol",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
        }
        if missing := required.difference(daily.columns):
            raise ValueError(f"daily market data is missing columns: {sorted(missing)}")
        frame = daily.copy()
        frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
        frame["symbol"] = frame["symbol"].astype(str)
        frame = frame.sort_values(["symbol", "date"]).reset_index(drop=True)
        for column, default in (
            ("industry", "unknown"),
            ("turnover_rate", np.nan),
            ("limit_up", False),
            ("limit_down", False),
            ("one_word_limit_up", False),
            ("one_word_limit_down", False),
            ("market_regime_alignment", 1.0),
        ):
            if column not in frame:
                frame[column] = default

        calculated = [
            self._calculate_symbol(section.copy())
            for _, section in frame.groupby("symbol", sort=False)
        ]
        output = pd.concat(calculated, ignore_index=True)
        output = self._cross_sectional_strength(output)
        output = self._benchmark_relative(output, benchmark)
        output = self._industry_relative(output)
        output = self._industry_rps(output, industry_rps)
        if financials is not None and not financials.empty:
            output = asof_join_available_data(output, financials)
        output = self._ensure_fundamentals(output)
        if valuations is not None and not valuations.empty:
            output = asof_join_available_data(output, valuations)
        output = self._valuation_percentiles(output)
        output["data_quality_risk"] = self._data_quality_risk(output)
        return output.sort_values(["date", "symbol"]).reset_index(drop=True)

    def _calculate_symbol(self, group: pd.DataFrame) -> pd.DataFrame:
        close = pd.to_numeric(group["close"], errors="coerce")
        high = pd.to_numeric(group["high"], errors="coerce")
        low = pd.to_numeric(group["low"], errors="coerce")
        open_ = pd.to_numeric(group["open"], errors="coerce")
        volume = pd.to_numeric(group["volume"], errors="coerce")
        amount = pd.to_numeric(group["amount"], errors="coerce")
        returns = close.pct_change(fill_method=None)
        previous_close = close.shift(1)
        true_range = pd.concat(
            [
                high - low,
                (high - previous_close).abs(),
                (low - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = true_range.rolling(14, min_periods=5).mean()
        ma20 = close.rolling(20, min_periods=5).mean()
        ma60 = close.rolling(60, min_periods=20).mean()
        previous_high_20 = high.shift(1).rolling(20, min_periods=10).max()
        previous_high_60 = high.shift(1).rolling(60, min_periods=20).max()

        group["ma20"] = ma20
        group["ma60"] = ma60
        group["atr_14"] = atr
        group["close_above_ma20"] = (close > ma20).astype(float)
        group["close_above_ma60"] = (close > ma60).astype(float)
        group["ma20_above_ma60"] = (ma20 > ma60).astype(float)
        group["ma20_slope"] = ma20.pct_change(5, fill_method=None) / 5
        group["ma60_slope"] = ma60.pct_change(10, fill_method=None) / 10
        group["distance_from_ma20"] = (close / ma20 - 1).abs()
        group["distance_from_ma60"] = (close / ma60 - 1).abs()
        rolling_52w_high = high.rolling(250, min_periods=60).max()
        group["distance_from_52w_high"] = (rolling_52w_high - close) / rolling_52w_high
        group["breakout_20d"] = (close > previous_high_20).astype(float)
        group["breakout_60d"] = (close > previous_high_60).astype(float)
        group["trend_duration"] = self._consecutive_true(close > ma20)
        group["higher_high"] = (
            high.rolling(5, min_periods=3).max() > high.shift(5).rolling(5, min_periods=3).max()
        ).astype(float)
        group["higher_low"] = (
            low.rolling(5, min_periods=3).min() > low.shift(5).rolling(5, min_periods=3).min()
        ).astype(float)
        for horizon in (5, 20, 60, 120):
            group[f"return_{horizon}d"] = close.pct_change(horizon, fill_method=None)

        volume_5 = volume.rolling(5, min_periods=3).mean()
        volume_20 = volume.rolling(20, min_periods=5).mean()
        amount_5 = amount.rolling(5, min_periods=3).mean()
        amount_20 = amount.rolling(20, min_periods=5).mean()
        group["volume_ratio_5d_20d"] = volume_5 / volume_20.replace(0, np.nan)
        group["amount_ratio_5d_20d"] = amount_5 / amount_20.replace(0, np.nan)
        turnover = pd.to_numeric(group["turnover_rate"], errors="coerce")
        group["turnover_percentile_120d"] = turnover.rolling(120, min_periods=20).apply(
            lambda values: pd.Series(values).rank(pct=True).iloc[-1], raw=False
        )
        up_volume = volume.where(returns > 0, 0).rolling(20, min_periods=5).sum()
        down_volume = volume.where(returns < 0, 0).rolling(20, min_periods=5).sum()
        group["up_volume_down_volume_ratio"] = up_volume / down_volume.replace(0, np.nan)
        group["breakout_volume_confirmation"] = (
            group[["breakout_20d", "breakout_60d"]].max(axis=1)
            * volume
            / volume_20.replace(0, np.nan)
        )
        group["pullback_volume_contraction"] = np.where(
            returns < 0, (1 - volume / volume_20.replace(0, np.nan)).clip(lower=0), 0.0
        )
        price_range = (high - low).replace(0, np.nan)
        group["close_location_value"] = (2 * close - high - low) / price_range
        upper_shadow = high - pd.concat([open_, close], axis=1).max(axis=1)
        group["high_volume_stall"] = (
            (volume > volume_20 * 1.8)
            & (returns.abs() < returns.rolling(20, min_periods=5).std())
            & (upper_shadow > price_range * 0.35)
        ).astype(float)
        group["long_upper_shadow_volume"] = (
            upper_shadow / price_range * (volume / volume_20.replace(0, np.nan))
        )
        group["amount_stability"] = 1 / (
            1
            + amount.rolling(20, min_periods=5).std()
            / amount.rolling(20, min_periods=5).mean().replace(0, np.nan)
        )

        group["atr_percent"] = atr / close
        group["volatility_20d"] = returns.rolling(20, min_periods=5).std() * np.sqrt(252)
        group["volatility_60d"] = returns.rolling(60, min_periods=20).std() * np.sqrt(252)
        group["downside_volatility"] = returns.where(returns < 0).rolling(
            60, min_periods=10
        ).std() * np.sqrt(252)
        rolling_peak = close.rolling(60, min_periods=20).max()
        group["max_drawdown_60d"] = (
            (close / rolling_peak - 1).rolling(60, min_periods=20).min().abs()
        )
        group["gap_risk"] = (open_ / previous_close - 1).abs().rolling(20, min_periods=5).max()
        group["limit_up_count_20d"] = (
            pd.Series(group["limit_up"], dtype=float).rolling(20, min_periods=1).sum()
        )
        group["limit_down_count_20d"] = (
            pd.Series(group["limit_down"], dtype=float).rolling(20, min_periods=1).sum()
        )
        one_word = pd.Series(group["one_word_limit_up"], dtype=bool) | pd.Series(
            group["one_word_limit_down"], dtype=bool
        )
        group["one_word_limit_count"] = one_word.astype(float).rolling(20, min_periods=1).sum()
        group["one_word_limit_consecutive"] = self._consecutive_true(one_word)
        group["distance_from_ma20_atr"] = (close - ma20).abs() / atr.replace(0, np.nan)
        group["event_risk"] = pd.to_numeric(
            group.get("event_risk", pd.Series(0.0, index=group.index)),
            errors="coerce",
        ).fillna(0.0)
        return group

    @staticmethod
    def _cross_sectional_strength(frame: pd.DataFrame) -> pd.DataFrame:
        for horizon in (20, 60, 120):
            frame[f"rps_{horizon}d"] = frame.groupby("date")[f"return_{horizon}d"].rank(pct=True)
        return frame

    @staticmethod
    def _benchmark_relative(frame: pd.DataFrame, benchmark: pd.DataFrame | None) -> pd.DataFrame:
        if benchmark is None or benchmark.empty:
            frame["relative_return_vs_csi300_20d"] = np.nan
            frame["relative_return_vs_csi300_60d"] = np.nan
            frame["down_market_relative_strength"] = np.nan
            return frame
        index = benchmark.copy()
        index["date"] = pd.to_datetime(index["date"]).dt.normalize()
        index = index.sort_values("date")
        index["benchmark_return_1d"] = index["close"].pct_change(fill_method=None)
        for horizon in (20, 60):
            index[f"benchmark_return_{horizon}d"] = index["close"].pct_change(
                horizon, fill_method=None
            )
        output = frame.merge(
            index[
                [
                    "date",
                    "benchmark_return_1d",
                    "benchmark_return_20d",
                    "benchmark_return_60d",
                ]
            ],
            on="date",
            how="left",
        )
        for horizon in (20, 60):
            output[f"relative_return_vs_csi300_{horizon}d"] = (
                output[f"return_{horizon}d"] - output[f"benchmark_return_{horizon}d"]
            )
        output["_daily_return"] = output.groupby("symbol")["close"].pct_change(fill_method=None)
        output["_down_relative"] = np.where(
            output["benchmark_return_1d"] < 0,
            output["_daily_return"] - output["benchmark_return_1d"],
            np.nan,
        )
        output["down_market_relative_strength"] = output.groupby("symbol")[
            "_down_relative"
        ].transform(lambda values: values.rolling(60, min_periods=10).mean())
        return output.drop(columns=["_daily_return", "_down_relative"])

    @staticmethod
    def _industry_relative(frame: pd.DataFrame) -> pd.DataFrame:
        for horizon in (20, 60):
            industry_mean = frame.groupby(["date", "industry"])[f"return_{horizon}d"].transform(
                "mean"
            )
            frame[f"relative_return_vs_industry_{horizon}d"] = (
                frame[f"return_{horizon}d"] - industry_mean
            )
        return frame

    @staticmethod
    def _industry_rps(frame: pd.DataFrame, industry_rps: pd.DataFrame | None) -> pd.DataFrame:
        columns = ["industry_rps_50", "industry_rps_120", "industry_rps_250"]
        if industry_rps is not None and not industry_rps.empty:
            source = industry_rps.copy()
            source["date"] = pd.to_datetime(source["date"]).dt.normalize()
            available = [column for column in columns if column in source]
            frame = frame.merge(
                source[["date", "industry", *available]],
                on=["date", "industry"],
                how="left",
            )
        for column in columns:
            if column not in frame:
                proxy = "rps_120d" if column == "industry_rps_120" else "rps_60d"
                frame[column] = frame.groupby(["date", "industry"])[proxy].transform("mean")
        return frame

    @staticmethod
    def _ensure_fundamentals(frame: pd.DataFrame) -> pd.DataFrame:
        columns = (
            "revenue_growth_yoy",
            "net_profit_growth_yoy",
            "deducted_profit_growth_yoy",
            "roe_ttm",
            "gross_margin_change",
            "operating_cashflow_to_profit",
            "free_cashflow",
            "debt_ratio",
            "inventory_growth",
            "receivable_growth",
            "goodwill_ratio",
            "non_recurring_profit_ratio",
        )
        for column in columns:
            if column not in frame:
                frame[column] = np.nan
        frame["primary_business_growth"] = (frame["revenue_growth_yoy"] > 0) & (
            frame["deducted_profit_growth_yoy"] > 0
        )
        frame["non_recurring_growth"] = (frame["net_profit_growth_yoy"] > 0) & (
            (frame["deducted_profit_growth_yoy"] <= 0) | (frame["non_recurring_profit_ratio"] > 0.3)
        )
        consolidation = frame.get("consolidation_change", pd.Series(False, index=frame.index))
        frame["consolidation_growth"] = pd.Series(
            consolidation,
            index=frame.index,
            dtype="boolean",
        ).fillna(False)
        prior_profit = pd.to_numeric(
            frame.get("prior_net_profit", pd.Series(np.nan, index=frame.index)),
            errors="coerce",
        )
        frame["low_base_growth"] = prior_profit.abs() < prior_profit.abs().quantile(0.1)
        frame["revenue_without_profit"] = (frame["revenue_growth_yoy"] > 0) & (
            frame["net_profit_growth_yoy"] <= 0
        )
        frame["cashflow_divergence"] = (frame["net_profit_growth_yoy"] > 0) & (
            frame["operating_cashflow_to_profit"] < 0.8
        )
        return frame

    @staticmethod
    def _valuation_percentiles(frame: pd.DataFrame) -> pd.DataFrame:
        raw_to_output = {
            "pe_ttm": "pe_ttm_percentile",
            "pb": "pb_percentile",
            "ps_ttm": "ps_ttm_percentile",
            "dividend_yield": "dividend_yield_percentile",
            "free_cashflow_yield": "free_cashflow_yield_percentile",
        }
        for raw, output in raw_to_output.items():
            if raw not in frame:
                frame[output] = np.nan
                continue
            values = pd.to_numeric(frame[raw], errors="coerce")
            if raw == "pe_ttm":
                values = values.where(values > 0)
            frame[output] = values.groupby([frame["date"], frame["industry"]]).rank(pct=True)
        return frame

    @staticmethod
    def _data_quality_risk(frame: pd.DataFrame) -> pd.Series:
        core = [
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "ma20",
            "ma60",
        ]
        return frame[core].isna().mean(axis=1)

    @staticmethod
    def _consecutive_true(values: pd.Series) -> pd.Series:
        group = (~values.fillna(False)).cumsum()
        return values.fillna(False).astype(int).groupby(group).cumsum()
