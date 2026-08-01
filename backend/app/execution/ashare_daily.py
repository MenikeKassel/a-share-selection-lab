from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd

from app.domain.protocols import BacktestRequest, BacktestResult


@dataclass(slots=True)
class _Position:
    symbol: str
    quantity: int
    average_cost: float
    last_buy_date: pd.Timestamp
    industry: str
    last_price: float
    adj_factor: float = 1.0


class AshareDailyExecutionEngine:
    """Formal daily A-share engine; signals are observed after the close."""

    engine_code = "ashare_daily_v1"

    def run(self, request: BacktestRequest) -> BacktestResult:
        if request.market_data_path is None or request.signal_path is None:
            raise ValueError("formal backtest requires market_data_path and signal_path")
        market = self._read_frame(request.market_data_path)
        signals = self._read_frame(request.signal_path)
        return self.run_with_data(request, market, signals)

    def run_with_data(
        self,
        request: BacktestRequest,
        market_data: pd.DataFrame,
        signals: pd.DataFrame,
        *,
        _calculate_theoretical: bool = True,
    ) -> BacktestResult:
        market = self._prepare_market(market_data, request)
        signal_frame = self._prepare_signals(signals, request)
        trade_dates = list(market["date"].drop_duplicates().sort_values())
        if not trade_dates:
            return self._empty_result(request)

        signal_targets = self._targets_by_execution_date(signal_frame, trade_dates, request.top_n)
        rows_by_date = {
            trade_date: day.set_index("symbol", drop=False)
            for trade_date, day in market.groupby("date", sort=True)
        }
        cash = float(request.initial_cash)
        positions: dict[str, _Position] = {}
        trades: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        position_history: list[dict[str, Any]] = []
        equity_curve: list[dict[str, Any]] = []
        holding_age: dict[str, int] = {}

        for trade_date in trade_dates:
            day = rows_by_date[trade_date]
            cash = self._apply_corporate_actions(day, positions, cash)

            if trade_date in signal_targets:
                target_symbols = signal_targets[trade_date]
                cash = self._rebalance(
                    trade_date=trade_date,
                    target_symbols=target_symbols,
                    day=day,
                    positions=positions,
                    cash=cash,
                    request=request,
                    trades=trades,
                    failures=failures,
                )
                holding_age = {symbol: holding_age.get(symbol, 0) for symbol in positions}

            for symbol in list(positions):
                holding_age[symbol] = holding_age.get(symbol, 0) + 1
                if holding_age[symbol] > request.holding_period:
                    sold, cash = self._sell_all(
                        trade_date,
                        symbol,
                        day,
                        positions,
                        cash,
                        request,
                        trades,
                        failures,
                        reason="holding_period_exit",
                    )
                    if sold:
                        holding_age.pop(symbol, None)

            self._mark_positions(day, positions)
            market_value = sum(
                position.quantity * position.last_price for position in positions.values()
            )
            total_equity = cash + market_value
            equity_curve.append(
                {
                    "date": trade_date.date().isoformat(),
                    "cash": round(cash, 4),
                    "market_value": round(market_value, 4),
                    "equity": round(total_equity, 4),
                }
            )
            position_history.extend(
                {
                    "date": trade_date.date().isoformat(),
                    "symbol": position.symbol,
                    "quantity": position.quantity,
                    "average_cost": round(position.average_cost, 6),
                    "close": round(position.last_price, 6),
                    "market_value": round(position.quantity * position.last_price, 4),
                    "industry": position.industry,
                }
                for position in positions.values()
            )

        tradable_return = equity_curve[-1]["equity"] / request.initial_cash - 1.0
        theoretical_return = tradable_return
        if _calculate_theoretical:
            frictionless_market = market_data.copy()
            for column in (
                "suspended",
                "limit_up",
                "limit_down",
                "one_word_limit_up",
                "one_word_limit_down",
            ):
                frictionless_market[column] = False
            frictionless_market["volume"] = (
                pd.to_numeric(frictionless_market["volume"], errors="coerce")
                .fillna(1.0)
                .clip(lower=1.0)
            )
            frictionless_request = replace(
                request,
                commission_rate=0.0,
                minimum_commission=0.0,
                stamp_tax_rate=0.0,
                slippage_bps=0.0,
            )
            frictionless = self.run_with_data(
                frictionless_request,
                frictionless_market,
                signals,
                _calculate_theoretical=False,
            )
            theoretical_return = float(frictionless.performance["tradable_return"])
        performance = self._performance(equity_curve, trades, theoretical_return, tradable_return)
        return BacktestResult(
            equity_curve=equity_curve,
            trades=trades,
            positions=position_history,
            performance=performance,
            execution_failures=failures,
            metadata={
                "engine": self.engine_code,
                "formal_ashare_result": True,
                "signal_timing": "after_close",
                "execution_timing": "next_trade_day_open",
                "theoretical_return_definition": (
                    "same portfolio path without price-limit/suspension/cost/slippage frictions"
                ),
                "rules": [
                    "T+1",
                    "100_share_board_lot",
                    "limit_up_buy_block",
                    "limit_down_sell_block",
                    "suspension",
                    "commission_and_minimum",
                    "stamp_tax_on_sell",
                    "slippage",
                    "corporate_actions",
                ],
            },
        )

    def _rebalance(
        self,
        *,
        trade_date: pd.Timestamp,
        target_symbols: list[str],
        day: pd.DataFrame,
        positions: dict[str, _Position],
        cash: float,
        request: BacktestRequest,
        trades: list[dict[str, Any]],
        failures: list[dict[str, Any]],
    ) -> float:
        for symbol in list(positions):
            if symbol not in target_symbols:
                _, cash = self._sell_all(
                    trade_date,
                    symbol,
                    day,
                    positions,
                    cash,
                    request,
                    trades,
                    failures,
                    reason="rebalance_exit",
                )

        open_market_value = 0.0
        for symbol, position in positions.items():
            open_price = (
                float(cast(Any, day.at[symbol, "open"]))
                if symbol in day.index
                else position.last_price
            )
            open_market_value += position.quantity * open_price
        open_equity = cash + open_market_value
        if not target_symbols:
            return cash
        equal_weight = min(1.0 / len(target_symbols), request.max_stock_weight)
        industry_allocated: dict[str, float] = {}
        for position in positions.values():
            mark_price = (
                float(cast(Any, day.at[position.symbol, "open"]))
                if position.symbol in day.index
                else position.last_price
            )
            industry_allocated[position.industry] = (
                industry_allocated.get(position.industry, 0.0) + position.quantity * mark_price
            )

        for symbol in target_symbols:
            if symbol not in day.index:
                failures.append(self._failure(trade_date, symbol, "missing_market_bar", "buy"))
                continue
            row = cast(pd.Series, day.loc[symbol])
            blocked_reason = self._buy_block_reason(row)
            if blocked_reason:
                failures.append(self._failure(trade_date, symbol, blocked_reason, "buy"))
                continue
            industry = str(row.get("industry", "unknown"))
            industry_room = request.max_industry_weight * open_equity - industry_allocated.get(
                industry, 0.0
            )
            target_value = min(equal_weight * open_equity, max(industry_room, 0.0))
            current = positions.get(symbol)
            current_value = 0.0 if current is None else current.quantity * float(row["open"])
            required_value = max(target_value - current_value, 0.0)
            buy_price = float(row["open"]) * (1.0 + request.slippage_bps / 10_000.0)
            quantity = int(required_value // (buy_price * 100)) * 100
            while quantity > 0:
                gross = quantity * buy_price
                commission = max(gross * request.commission_rate, request.minimum_commission)
                if gross + commission <= cash:
                    break
                quantity -= 100
            if quantity <= 0:
                failures.append(self._failure(trade_date, symbol, "insufficient_cash", "buy"))
                continue
            gross = quantity * buy_price
            commission = max(gross * request.commission_rate, request.minimum_commission)
            cash -= gross + commission
            if current is None:
                positions[symbol] = _Position(
                    symbol=symbol,
                    quantity=quantity,
                    average_cost=(gross + commission) / quantity,
                    last_buy_date=trade_date,
                    industry=industry,
                    last_price=float(row["open"]),
                    adj_factor=float(row.get("adj_factor", 1.0) or 1.0),
                )
            else:
                old_cost = current.average_cost * current.quantity
                current.quantity += quantity
                current.average_cost = (old_cost + gross + commission) / current.quantity
                current.last_buy_date = trade_date
            industry_allocated[industry] = industry_allocated.get(industry, 0.0) + gross
            trades.append(
                self._trade(
                    trade_date,
                    symbol,
                    "buy",
                    quantity,
                    buy_price,
                    commission,
                    0.0,
                    "rebalance_entry",
                )
            )
        return cash

    def _sell_all(
        self,
        trade_date: pd.Timestamp,
        symbol: str,
        day: pd.DataFrame,
        positions: dict[str, _Position],
        cash: float,
        request: BacktestRequest,
        trades: list[dict[str, Any]],
        failures: list[dict[str, Any]],
        *,
        reason: str,
    ) -> tuple[bool, float]:
        position = positions[symbol]
        if position.last_buy_date >= trade_date:
            failures.append(self._failure(trade_date, symbol, "t_plus_one", "sell"))
            return False, cash
        if symbol not in day.index:
            failures.append(self._failure(trade_date, symbol, "missing_market_bar", "sell"))
            return False, cash
        row = cast(pd.Series, day.loc[symbol])
        blocked_reason = self._sell_block_reason(row)
        if blocked_reason:
            failures.append(self._failure(trade_date, symbol, blocked_reason, "sell"))
            return False, cash
        sell_price = float(row["open"]) * (1.0 - request.slippage_bps / 10_000.0)
        gross = position.quantity * sell_price
        commission = max(gross * request.commission_rate, request.minimum_commission)
        stamp_tax = gross * request.stamp_tax_rate
        cash += gross - commission - stamp_tax
        trades.append(
            self._trade(
                trade_date,
                symbol,
                "sell",
                position.quantity,
                sell_price,
                commission,
                stamp_tax,
                reason,
            )
        )
        del positions[symbol]
        return True, cash

    @staticmethod
    def _buy_block_reason(row: pd.Series) -> str | None:
        if bool(row.get("suspended", False)) or float(row.get("volume", 0)) <= 0:
            return "suspended"
        if bool(row.get("limit_up", False)) or bool(row.get("one_word_limit_up", False)):
            return "limit_up_unbuyable"
        return None

    @staticmethod
    def _sell_block_reason(row: pd.Series) -> str | None:
        if bool(row.get("suspended", False)) or float(row.get("volume", 0)) <= 0:
            return "suspended"
        if bool(row.get("limit_down", False)) or bool(row.get("one_word_limit_down", False)):
            return "limit_down_unsellable"
        return None

    @staticmethod
    def _apply_corporate_actions(
        day: pd.DataFrame, positions: dict[str, _Position], cash: float
    ) -> float:
        for symbol, position in positions.items():
            if symbol not in day.index:
                continue
            row = day.loc[symbol]
            new_factor = float(row.get("adj_factor", position.adj_factor) or position.adj_factor)
            if position.adj_factor > 0 and new_factor > 0 and new_factor != position.adj_factor:
                adjusted = int(position.quantity * new_factor / position.adj_factor)
                position.quantity = adjusted
                position.adj_factor = new_factor
            dividend = float(row.get("cash_dividend_per_share", 0.0) or 0.0)
            cash += position.quantity * dividend
        return cash

    @staticmethod
    def _mark_positions(day: pd.DataFrame, positions: dict[str, _Position]) -> None:
        for symbol, position in positions.items():
            if symbol in day.index and pd.notna(day.loc[symbol, "close"]):
                position.last_price = float(cast(Any, day.at[symbol, "close"]))

    @staticmethod
    def _targets_by_execution_date(
        signals: pd.DataFrame, trade_dates: list[pd.Timestamp], top_n: int
    ) -> dict[pd.Timestamp, list[str]]:
        next_date = {current: following for current, following in pairwise(trade_dates)}
        targets: dict[pd.Timestamp, list[str]] = {}
        for signal_date, group in signals.groupby("signal_date", sort=True):
            execution_date = next_date.get(pd.Timestamp(str(signal_date)))
            if execution_date is None:
                continue
            targets[execution_date] = (
                group.sort_values(["score", "symbol"], ascending=[False, True])
                .head(top_n)["symbol"]
                .astype(str)
                .tolist()
            )
        return targets

    @staticmethod
    def _prepare_market(market_data: pd.DataFrame, request: BacktestRequest) -> pd.DataFrame:
        required = {"date", "symbol", "open", "close", "volume"}
        missing = required.difference(market_data.columns)
        if missing:
            raise ValueError(f"market data is missing columns: {sorted(missing)}")
        market = market_data.copy()
        market["date"] = pd.to_datetime(market["date"]).dt.normalize()
        market["symbol"] = market["symbol"].astype(str)
        start = pd.Timestamp(request.start_date)
        end = pd.Timestamp(request.end_date)
        market = market.loc[market["date"].between(start, end)]
        return market.sort_values(["date", "symbol"]).reset_index(drop=True)

    @staticmethod
    def _prepare_signals(signals: pd.DataFrame, request: BacktestRequest) -> pd.DataFrame:
        required = {"signal_date", "symbol", "score"}
        missing = required.difference(signals.columns)
        if missing:
            raise ValueError(f"signals are missing columns: {sorted(missing)}")
        output = signals.copy()
        output["signal_date"] = pd.to_datetime(output["signal_date"]).dt.normalize()
        output["symbol"] = output["symbol"].astype(str)
        start = pd.Timestamp(request.start_date)
        end = pd.Timestamp(request.end_date)
        output = output.loc[output["signal_date"].between(start, end)].copy()
        if request.rebalance_frequency == "weekly":
            weekly_dates = (
                output.assign(_week=output["signal_date"].dt.to_period("W-FRI"))
                .groupby("_week", observed=True)["signal_date"]
                .max()
            )
            output = output.loc[output["signal_date"].isin(weekly_dates)]
        elif request.rebalance_frequency != "daily":
            raise ValueError("rebalance_frequency must be daily or weekly")
        return output.sort_values(["signal_date", "score"], ascending=[True, False])

    @staticmethod
    def _performance(
        equity_curve: list[dict[str, Any]],
        trades: list[dict[str, Any]],
        theoretical_return: float,
        tradable_return: float,
    ) -> dict[str, Any]:
        equities = pd.Series([float(item["equity"]) for item in equity_curve], dtype=float)
        daily_returns = equities.pct_change().dropna()
        drawdown = equities / equities.cummax() - 1.0
        years = max(len(equities) / 252.0, 1 / 252.0)
        annualized = (1.0 + tradable_return) ** (1.0 / years) - 1.0
        sharpe = (
            float(daily_returns.mean() / daily_returns.std(ddof=0) * np.sqrt(252))
            if len(daily_returns) > 1 and daily_returns.std(ddof=0) > 0
            else 0.0
        )
        sell_trades = [item for item in trades if item["side"] == "sell"]
        return {
            "theoretical_return": theoretical_return,
            "tradable_return": tradable_return,
            "annualized_return": annualized,
            "max_drawdown": float(drawdown.min()) if not drawdown.empty else 0.0,
            "sharpe": sharpe,
            "trade_count": len(trades),
            "closed_trade_count": len(sell_trades),
            "total_fees": sum(
                float(item["commission"]) + float(item["stamp_tax"]) for item in trades
            ),
        }

    @staticmethod
    def _trade(
        trade_date: pd.Timestamp,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        commission: float,
        stamp_tax: float,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "trade_date": trade_date.date().isoformat(),
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": round(price, 6),
            "gross_amount": round(quantity * price, 4),
            "commission": round(commission, 4),
            "stamp_tax": round(stamp_tax, 4),
            "reason": reason,
        }

    @staticmethod
    def _failure(trade_date: pd.Timestamp, symbol: str, reason: str, side: str) -> dict[str, Any]:
        return {
            "trade_date": trade_date.date().isoformat(),
            "symbol": symbol,
            "side": side,
            "reason": reason,
        }

    @staticmethod
    def _read_frame(path_value: str) -> pd.DataFrame:
        path = Path(path_value)
        if path.suffix.lower() == ".parquet":
            return pd.read_parquet(path)
        if path.suffix.lower() == ".csv":
            return pd.read_csv(path)
        raise ValueError(f"unsupported data file: {path.suffix}")

    @staticmethod
    def _empty_result(request: BacktestRequest) -> BacktestResult:
        return BacktestResult(
            equity_curve=[],
            trades=[],
            positions=[],
            performance={
                "theoretical_return": 0.0,
                "tradable_return": 0.0,
                "trade_count": 0,
            },
            execution_failures=[],
            metadata={
                "engine": AshareDailyExecutionEngine.engine_code,
                "formal_ashare_result": True,
                "initial_cash": request.initial_cash,
            },
        )
