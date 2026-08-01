from __future__ import annotations

from dataclasses import asdict
from importlib.util import find_spec
from itertools import product
from typing import Any

import numpy as np
import pandas as pd

from app.adapters import OptionalEngineUnavailableError
from app.adapters.vectorbt.schemas import (
    VectorBTParameterSet,
    VectorBTResearchResult,
)
from app.adapters.vectorbt.signal_converter import convert_scores_to_signals


class VectorBTResearchAdapter:
    """Fast parameter research; never emits a formal A-share result."""

    engine_code = "vectorbt"

    @staticmethod
    def is_available() -> bool:
        return find_spec("vectorbt") is not None

    def run_parameter_scan(
        self,
        prices: pd.DataFrame,
        scores: pd.DataFrame,
        parameter_grid: dict[str, list[Any]],
        *,
        initial_cash: float = 1_000_000.0,
    ) -> list[VectorBTResearchResult]:
        if not self.is_available():
            raise OptionalEngineUnavailableError(
                "VectorBT 未安装；运行 uv sync --extra fast-backtest。"
            )
        parameters = self._expand_grid(parameter_grid)
        return [
            self.run(prices, scores, parameter, initial_cash=initial_cash)
            for parameter in parameters
        ]

    def run(
        self,
        prices: pd.DataFrame,
        scores: pd.DataFrame,
        parameter_set: VectorBTParameterSet,
        *,
        initial_cash: float = 1_000_000.0,
    ) -> VectorBTResearchResult:
        if not self.is_available():
            raise OptionalEngineUnavailableError(
                "VectorBT 未安装；运行 uv sync --extra fast-backtest。"
            )
        # Third-party import is intentionally confined to this adapter.
        import vectorbt as vbt

        prepared_scores, preparation_metadata = self.prepare_scores(scores, parameter_set)
        signal = convert_scores_to_signals(
            prepared_scores,
            prices,
            top_n=parameter_set.top_n,
            holding_period=parameter_set.holding_period,
            rebalance_frequency=parameter_set.rebalance_frequency,
        )
        portfolio = vbt.Portfolio.from_signals(
            signal.close,
            entries=signal.entries,
            exits=signal.exits,
            init_cash=initial_cash,
            fees=parameter_set.commission_rate,
            slippage=parameter_set.slippage_bps / 10_000.0,
            cash_sharing=True,
            group_by=True,
            size=1.0 / parameter_set.top_n,
            size_type="percent",
            call_seq="auto",
            freq="1D",
        )
        cumulative = self._scalar(portfolio.total_return(group_by=True))
        max_drawdown = abs(self._scalar(portfolio.max_drawdown(group_by=True)))
        annualized = self._scalar(portfolio.annualized_return(group_by=True))
        sharpe = self._scalar(portfolio.sharpe_ratio(group_by=True))
        trade_count = int(self._scalar(portfolio.trades.count(group_by=True)))
        win_rate = self._scalar(portfolio.trades.win_rate(group_by=True))
        changes = signal.entries.astype(int) + signal.exits.astype(int)
        turnover = float(changes.sum(axis=1).mean() / max(parameter_set.top_n, 1))
        return VectorBTResearchResult(
            parameter_set=asdict(parameter_set),
            cumulative_return=cumulative,
            annualized_return=annualized,
            max_drawdown=max_drawdown,
            sharpe=sharpe,
            turnover=turnover,
            trade_count=trade_count,
            win_rate=win_rate,
            metadata={
                **signal.metadata,
                **preparation_metadata,
                "research_engine": self.engine_code,
                "formal_ashare_validation": "required",
                "formal_result": False,
                "limitations": [
                    "不含完整A股涨跌停与一字板撮合",
                    "不替代T+1和整手约束复核",
                    "研究结果不得自动升级为正式策略",
                ],
            },
        )

    @staticmethod
    def prepare_scores(
        scores: pd.DataFrame,
        parameter_set: VectorBTParameterSet,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Apply research-only weights and eligibility thresholds before ranking."""

        prepared = scores.copy()
        required = {"signal_date", "symbol", "score"}
        if missing := required.difference(prepared.columns):
            raise ValueError(f"score frame is missing columns: {sorted(missing)}")

        metadata: dict[str, Any] = {
            "input_rows": len(prepared),
            "factor_weights_applied": None,
            "filters_applied": [],
        }
        if parameter_set.factor_weights:
            factor_columns = list(parameter_set.factor_weights)
            if missing := set(factor_columns).difference(prepared.columns):
                raise ValueError(
                    f"factor_weights refer to missing score columns: {sorted(missing)}"
                )
            weighted = pd.Series(0.0, index=prepared.index)
            for column, weight in parameter_set.factor_weights.items():
                weighted = weighted + pd.to_numeric(prepared[column], errors="coerce").fillna(
                    0.0
                ) * float(weight)
            prepared["score"] = weighted
            metadata["factor_weights_applied"] = dict(parameter_set.factor_weights)

        filters: list[tuple[str, str, float | None]] = [
            ("atr_percent", "<=", parameter_set.atr_threshold),
            (
                "breakout_volume_confirmation",
                ">=",
                parameter_set.breakout_volume_ratio,
            ),
            ("pa_score", ">=", parameter_set.pa_score_threshold),
            ("risk_penalty", "<=", parameter_set.risk_penalty_threshold),
        ]
        for column, operator, threshold in filters:
            if threshold is None:
                continue
            if column not in prepared:
                raise ValueError(f"parameter threshold requires score column: {column}")
            numeric = pd.to_numeric(prepared[column], errors="coerce")
            if operator == "<=":
                prepared = prepared.loc[numeric <= threshold]
            else:
                prepared = prepared.loc[numeric >= threshold]
            metadata["filters_applied"].append(f"{column}{operator}{float(threshold)}")
        metadata["eligible_rows"] = len(prepared)
        return prepared, metadata

    @staticmethod
    def _expand_grid(grid: dict[str, list[Any]]) -> list[VectorBTParameterSet]:
        defaults: dict[str, list[Any]] = {
            "top_n": [20],
            "holding_period": [5],
            "rebalance_frequency": ["daily"],
            "commission_rate": [0.0003],
            "slippage_bps": [5.0],
        }
        values = {**defaults, **grid}
        keys = list(values)
        output = []
        for combination in product(*(values[key] for key in keys)):
            item = dict(zip(keys, combination, strict=True))
            output.append(VectorBTParameterSet(**item))
        return output

    @staticmethod
    def _scalar(value: Any) -> float:
        if isinstance(value, pd.Series):
            value = value.iloc[0]
        if isinstance(value, np.ndarray):
            value = value.flat[0]
        numeric = float(value)
        return numeric if np.isfinite(numeric) else 0.0
