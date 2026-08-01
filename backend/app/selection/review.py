from __future__ import annotations

from typing import Any, ClassVar

import numpy as np
import pandas as pd


class AutomaticReviewService:
    default_horizons: ClassVar[list[int]] = [1, 3, 5, 10, 20, 60]

    def calculate(
        self,
        *,
        candidates: list[dict[str, object]],
        market: pd.DataFrame,
        horizons: list[int] | None = None,
        benchmark_symbol: str = "000300.SH",
    ) -> pd.DataFrame:
        bars = market.copy()
        bars["date"] = pd.to_datetime(bars["date"]).dt.normalize()
        bars["symbol"] = bars["symbol"].astype(str)
        output: list[dict[str, object]] = []
        for candidate in candidates:
            symbol = str(candidate["symbol"])
            signal_date = pd.Timestamp(str(candidate["selection_date"])).normalize()
            symbol_bars = bars.loc[bars["symbol"] == symbol].sort_values("date")
            dates = symbol_bars["date"].tolist()
            later_dates = [item for item in dates if item > signal_date]
            signal_rows = symbol_bars.loc[symbol_bars["date"] == signal_date]
            if not later_dates or signal_rows.empty:
                continue
            signal_close = float(signal_rows.iloc[-1]["close"])
            entry_date = later_dates[0]
            entry = symbol_bars.loc[symbol_bars["date"] == entry_date].iloc[0]
            entry_open = float(entry["open"])
            entry_failure_reason = self._entry_failure_reason(entry)
            entry_tradable = entry_failure_reason is None
            strategy_values = candidate.get("strategies", [])
            strategies = (
                [str(item) for item in strategy_values] if isinstance(strategy_values, list) else []
            )
            signals = self._candidate_signals(candidate)
            for horizon in horizons or self.default_horizons:
                window_dates = later_dates[:horizon]
                if len(window_dates) < horizon:
                    continue
                window = symbol_bars.loc[symbol_bars["date"].isin(window_dates)]
                exit_row = window.iloc[-1]
                close_to_close = float(exit_row["close"]) / signal_close - 1.0
                open_to_close = float(exit_row["close"]) / entry_open - 1.0
                close_series = window["close"].astype(float)
                drawdown = close_series / close_series.cummax() - 1.0
                benchmark_return = self._benchmark_return(
                    bars, benchmark_symbol, signal_date, window_dates[-1]
                )
                industry_return = self._industry_return(
                    bars,
                    str(candidate.get("industry", "unknown")),
                    signal_date,
                    window_dates[-1],
                )
                output.append(
                    {
                        "symbol": symbol,
                        "selection_date": signal_date.date().isoformat(),
                        "horizon": horizon,
                        "next_open_return": entry_open / signal_close - 1.0,
                        "open_to_close_return": open_to_close,
                        "close_to_close_return": close_to_close,
                        "max_favorable_excursion": float(window["high"].max()) / entry_open - 1.0,
                        "max_adverse_excursion": float(window["low"].min()) / entry_open - 1.0,
                        "period_max_drawdown": abs(float(drawdown.min())),
                        "benchmark_excess_return": close_to_close - benchmark_return,
                        "industry_excess_return": close_to_close - industry_return,
                        "tradable_return": open_to_close if entry_tradable else np.nan,
                        "tradable": entry_tradable,
                        "entry_failure_reason": entry_failure_reason,
                        "strategies": strategies,
                        "signals": signals,
                        "data_confidence": str(candidate.get("data_confidence", "unknown")),
                    }
                )
        return pd.DataFrame(output)

    def summarize(self, reviews: pd.DataFrame) -> dict[str, Any]:
        if reviews.empty:
            return {
                "overall": {},
                "strategy_results": {},
                "signal_results": {},
                "untradable_cases": [],
                "failed_breakout_cases": [],
                "data_anomaly_cases": [],
            }
        frame = reviews.copy()
        frame["_all"] = [["all"] for _ in range(len(frame))]
        return {
            "overall": self._aggregate(frame, "_all").get("all", {}),
            "strategy_results": self._aggregate(frame, "strategies"),
            "signal_results": self._aggregate(frame, "signals"),
            "untradable_cases": frame.loc[
                ~frame["tradable"].astype(bool),
                [
                    "symbol",
                    "selection_date",
                    "horizon",
                    "entry_failure_reason",
                ],
            ].to_dict(orient="records"),
            "failed_breakout_cases": frame.loc[
                frame["signals"].map(lambda values: "failed_breakout" in values),
                ["symbol", "selection_date", "horizon", "tradable_return"],
            ].to_dict(orient="records"),
            "data_anomaly_cases": frame.loc[
                frame["data_confidence"].isin(["blocked", "invalid"]),
                ["symbol", "selection_date", "horizon", "data_confidence"],
            ].to_dict(orient="records"),
        }

    @staticmethod
    def _aggregate(
        frame: pd.DataFrame, label_column: str
    ) -> dict[str, dict[str, dict[str, float | int]]]:
        exploded = frame.explode(label_column)
        exploded = exploded.loc[exploded[label_column].notna()]
        output: dict[str, dict[str, dict[str, float | int]]] = {}
        for (label, horizon), section in exploded.groupby([label_column, "horizon"], observed=True):
            tradable = pd.to_numeric(section["tradable_return"], errors="coerce").dropna()
            output.setdefault(str(label), {})[f"{int(str(horizon))}D"] = {
                "count": len(section),
                "tradable_count": len(tradable),
                "win_rate": (float((tradable > 0).mean()) if not tradable.empty else 0.0),
                "mean_tradable_return": (float(tradable.mean()) if not tradable.empty else 0.0),
                "mean_benchmark_excess_return": float(
                    pd.to_numeric(section["benchmark_excess_return"], errors="coerce").mean()
                ),
                "mean_industry_excess_return": float(
                    pd.to_numeric(section["industry_excess_return"], errors="coerce").mean()
                ),
            }
        return output

    @staticmethod
    def _entry_failure_reason(entry: pd.Series) -> str | None:
        if bool(entry.get("suspended", False)) or float(entry.get("volume", 1)) <= 0:
            return "suspended"
        if bool(entry.get("limit_up", False)) or bool(entry.get("one_word_limit_up", False)):
            return "limit_up_unbuyable"
        return None

    @staticmethod
    def _candidate_signals(candidate: dict[str, object]) -> list[str]:
        signals = [
            name
            for name in (
                "higher_high",
                "higher_low",
                "lower_high",
                "lower_low",
                "failed_breakout",
                "high_volume_stall",
            )
            if bool(candidate.get(name, False))
        ]
        wyckoff = candidate.get("wyckoff_candidates", [])
        if isinstance(wyckoff, list):
            signals.extend(
                str(item["signal_type"])
                for item in wyckoff
                if isinstance(item, dict) and item.get("signal_type")
            )
        minute = candidate.get("minute_features", {})
        if isinstance(minute, dict):
            closing = minute.get("closing_strength", {})
            if isinstance(closing, dict) and closing.get("close_above_vwap"):
                signals.append("close_above_vwap")
            tpo = minute.get("tpo_profile", {})
            if isinstance(tpo, dict) and tpo.get("value_area_migration") is not None:
                signals.append("value_area_migration")
        return sorted(set(signals))

    @staticmethod
    def _benchmark_return(
        bars: pd.DataFrame,
        benchmark_symbol: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> float:
        benchmark = bars.loc[
            (bars["symbol"] == benchmark_symbol) & bars["date"].between(start, end)
        ].sort_values("date")
        if len(benchmark) < 2:
            return 0.0
        return float(benchmark.iloc[-1]["close"] / benchmark.iloc[0]["close"] - 1.0)

    @staticmethod
    def _industry_return(
        bars: pd.DataFrame,
        industry: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> float:
        if "industry" not in bars or industry == "unknown":
            return 0.0
        peers = bars.loc[
            (bars["industry"].astype(str) == industry) & bars["date"].between(start, end)
        ].copy()
        if peers.empty:
            return 0.0
        returns = peers.groupby("symbol")["close"].agg(
            lambda values: values.iloc[-1] / values.iloc[0] - 1 if len(values) >= 2 else np.nan
        )
        result = float(returns.mean())
        return result if np.isfinite(result) else 0.0
