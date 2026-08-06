"""ashare_daily_v2: total-return-proxy formal execution engine (PR 1+2+4).

This is the v2 proxy engine used by v8 after the TinyShare corporate-action
probe failed (license expired).  Contract:

- tradability is decided ONLY from open-time information: suspension,
  zero volume, missing/non-positive open, and (in daily_conservative mode)
  open at limit;
- close-limit / one-word flags are never used to block next-open fills
  (they are close-time information);
- position quantities are NEVER changed from ``adj_factor``; no cash
  dividend is paid in proxy mode (the causal adjusted total-return series
  already carries the economic return, paying cash would double count);
- every buy registers a FIFO lot with entry_raw_notional, entry_adj_open
  and entry_raw_quantity; every sell emits realized PnL from the ledger;
- the report is marked proxy/reduced, never formal_ashare_result=true.

The strict engine (ashare_daily_v2_strict) will share this file's helpers
but is only instantiated when explicit corporate-action events are
available; it stays blocked in v8.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd

from app.domain.protocols import BacktestRequest, BacktestResult
from app.execution.corporate_actions import (
    ProxyLotRecord,
    proxy_exit_value,
    proxy_market_value,
)
from app.execution.lot_ledger import LotLedger, PositionLot

ENGINE_CODE_PROXY = "ashare_daily_v2_proxy"
ENGINE_CODE_STRICT = "ashare_daily_v2_strict"


@dataclass(slots=True)
class _Holding:
    """Proxy-mode holding: one or more lots for a symbol."""

    symbol: str
    industry: str
    last_buy_date: Any
    lots: list[PositionLot]
    # PR 4.1: raw and causal-adjusted closes tracked separately.  Proxy
    # valuation may only fall back on the adjusted close; the raw close is
    # informational (position history) and must never feed proxy_market_value.
    last_raw_close: float = 0.0
    last_adj_close: float = 0.0

    @property
    def quantity(self) -> int:
        return sum(lot.quantity_remaining for lot in self.lots)

    @property
    def raw_notional(self) -> float:
        return sum(lot.raw_notional for lot in self.lots)


class AshareDailyV2ProxyEngine:
    """Formal A-share engine, total-return-proxy mode, next-open fills."""

    engine_code = ENGINE_CODE_PROXY
    corporate_action_mode = "total_return_proxy"
    execution_confidence = "reduced"

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
        ledger = LotLedger()
        holdings: dict[str, _Holding] = {}
        trades: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        position_history: list[dict[str, Any]] = []
        equity_curve: list[dict[str, Any]] = []
        holding_age: dict[str, int] = {}

        for trade_date in trade_dates:
            day = rows_by_date[trade_date]
            # Proxy mode: no corporate-action quantity/dividend adjustments.
            if trade_date in signal_targets:
                target_symbols = signal_targets[trade_date]
                cash = self._rebalance(
                    trade_date=trade_date,
                    target_symbols=target_symbols,
                    day=day,
                    holdings=holdings,
                    ledger=ledger,
                    cash=cash,
                    request=request,
                    trades=trades,
                    failures=failures,
                )
                holding_age = {symbol: holding_age.get(symbol, 0) for symbol in holdings}

            for symbol in list(holdings):
                holding_age[symbol] = holding_age.get(symbol, 0) + 1
                if holding_age[symbol] > request.holding_period:
                    sold, cash = self._sell_all(
                        trade_date,
                        symbol,
                        day,
                        holdings,
                        ledger,
                        cash,
                        request,
                        trades,
                        failures,
                        reason="holding_period_exit",
                    )
                    if sold:
                        holding_age.pop(symbol, None)

            self._mark_positions(day, holdings)
            market_value = self._market_value(day, holdings)
            total_equity = cash + market_value
            equity_curve.append(
                {
                    "date": trade_date.date().isoformat(),
                    "cash": round(cash, 4),
                    "market_value": round(market_value, 4),
                    "equity": round(total_equity, 4),
                }
            )
            for symbol, holding in holdings.items():
                position_history.append(
                    {
                        "date": trade_date.date().isoformat(),
                        "symbol": symbol,
                        "quantity": holding.quantity,
                        "average_cost": round(self._average_cost(holding), 6),
                        "close": round(holding.last_raw_close, 6),
                        "market_value": round(self._market_value_of(day, holding), 4),
                        "industry": holding.industry,
                        "proxy_raw_notional": round(holding.raw_notional, 4),
                    }
                )

        tradable_return = equity_curve[-1]["equity"] / request.initial_cash - 1.0
        theoretical_return = tradable_return
        if _calculate_theoretical:
            frictionless_market = market_data.copy()
            for column in (
                "suspended",
                "close_at_limit_up",
                "close_at_limit_down",
                "open_at_limit_up",
                "open_at_limit_down",
            ):
                if column in frictionless_market:
                    frictionless_market[column] = False
            frictionless_market["volume"] = (
                pd.to_numeric(frictionless_market["volume"], errors="coerce")
                .fillna(1.0)
                .clip(lower=1.0)
            )
            frictionless_request = _frictionless(request)
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
            metadata=self._metadata(request),
        )

    def _rebalance(
        self,
        *,
        trade_date: Any,
        target_symbols: list[str],
        day: pd.DataFrame,
        holdings: dict[str, _Holding],
        ledger: LotLedger,
        cash: float,
        request: BacktestRequest,
        trades: list[dict[str, Any]],
        failures: list[dict[str, Any]],
    ) -> float:
        for symbol in list(holdings):
            if symbol not in target_symbols:
                _, cash = self._sell_all(
                    trade_date,
                    symbol,
                    day,
                    holdings,
                    ledger,
                    cash,
                    request,
                    trades,
                    failures,
                    reason="rebalance_exit",
                )

        open_equity = cash + self._market_value(day, holdings)
        if not target_symbols:
            return cash
        equal_weight = min(1.0 / len(target_symbols), request.max_stock_weight)
        industry_allocated: dict[str, float] = {}
        for holding in holdings.values():
            if holding.industry not in industry_allocated:
                industry_allocated[holding.industry] = 0.0
            if holding.symbol in day.index:
                industry_allocated[holding.industry] += self._market_value_of(day, holding)

        for symbol in target_symbols:
            if symbol not in day.index:
                failures.append(self._failure(trade_date, symbol, "missing_market_bar", "buy"))
                continue
            row = cast(pd.Series, day.loc[symbol])
            blocked_reason = self._buy_block_reason(row, request)
            if blocked_reason:
                failures.append(self._failure(trade_date, symbol, blocked_reason, "buy"))
                continue
            industry = str(row.get("industry", "unknown"))
            industry_room = request.max_industry_weight * open_equity - industry_allocated.get(
                industry, 0.0
            )
            target_value = min(equal_weight * open_equity, max(industry_room, 0.0))
            existing = holdings.get(symbol)
            current_value = 0.0
            if existing is not None:
                adj_open = self._adj_open(row)
                if adj_open > 0:
                    current_value = sum(
                        proxy_market_value(
                            ProxyLotRecord(
                                entry_raw_notional=lot.raw_notional,
                                entry_adj_open=lot.adj_open,
                                entry_raw_quantity=lot.quantity_remaining,
                            ),
                            adj_open,
                        )
                        for lot in existing.lots
                    )
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
            adj_open = self._adj_open(row)
            # PR 5.4: never fall back to the raw open.  A non-finite or
            # non-positive adj_open means the causal total-return scale is
            # unavailable; block the buy instead of silently mixing scales.
            if not np.isfinite(adj_open) or adj_open <= 0:
                failures.append(self._failure(trade_date, symbol, "missing_adj_open", "buy"))
                cash += gross + commission
                continue
            lot = PositionLot(
                lot_id="",
                symbol=symbol,
                entry_date=trade_date,
                quantity_remaining=quantity,
                entry_price=buy_price,
                allocated_buy_commission=commission,
                unit_cost=(gross + commission) / quantity,
                industry_at_entry=industry,
                # PR 4.1: price-only notional so proxy market value does not
                # inflate by the buy commission; the commission is already
                # inside unit_cost and was deducted from cash.
                raw_notional=gross,
                adj_open=adj_open,
            )
            ledger.add_lot(lot)
            if existing is None:
                holdings[symbol] = _Holding(
                    symbol=symbol,
                    industry=industry,
                    last_buy_date=trade_date,
                    lots=[lot],
                    last_raw_close=float(row["open"]),
                    last_adj_close=adj_open,
                )
            else:
                existing.lots.append(lot)
                existing.last_buy_date = trade_date
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
                    lot_id=lot.lot_id,
                )
            )
        return cash

    def _sell_all(
        self,
        trade_date: Any,
        symbol: str,
        day: pd.DataFrame,
        holdings: dict[str, _Holding],
        ledger: LotLedger,
        cash: float,
        request: BacktestRequest,
        trades: list[dict[str, Any]],
        failures: list[dict[str, Any]],
        *,
        reason: str,
    ) -> tuple[bool, float]:
        holding = holdings.get(symbol)
        if holding is None:
            return False, cash
        if holding.last_buy_date >= trade_date:
            failures.append(self._failure(trade_date, symbol, "t_plus_one", "sell"))
            return False, cash
        if symbol not in day.index:
            failures.append(self._failure(trade_date, symbol, "missing_market_bar", "sell"))
            return False, cash
        row = cast(pd.Series, day.loc[symbol])
        blocked_reason = self._sell_block_reason(row, request)
        if blocked_reason:
            failures.append(self._failure(trade_date, symbol, blocked_reason, "sell"))
            return False, cash
        adj_open = self._adj_open(row)
        # PR 5.4: value-level check - NaN/Inf is as unusable as <= 0.
        if not np.isfinite(adj_open) or adj_open <= 0:
            failures.append(self._failure(trade_date, symbol, "missing_adj_open", "sell"))
            return False, cash

        quantity = holding.quantity
        raw_open = float(row["open"])
        exit_value = sum(
            proxy_exit_value(
                ProxyLotRecord(
                    entry_raw_notional=lot.raw_notional,
                    entry_adj_open=lot.adj_open,
                    entry_raw_quantity=lot.quantity_remaining,
                ),
                adj_open,
            )
            for lot in holding.lots
        )
        commission = max(exit_value * request.commission_rate, request.minimum_commission)
        stamp_tax = exit_value * request.stamp_tax_rate
        cash += exit_value - commission - stamp_tax
        matched_cost = sum(lot.quantity_cost(lot.quantity_remaining) for lot in holding.lots)
        buy_commission_allocated = sum(
            lot.allocated_buy_commission for lot in holding.lots
        )
        realized_pnl = exit_value - matched_cost - commission - stamp_tax
        sale_result = {
            "matched_quantity": quantity,
            "matched_cost": matched_cost,
            "sell_gross": exit_value,
            "buy_commission_allocated": buy_commission_allocated,
            "sell_commission": commission,
            "stamp_tax": stamp_tax,
            "realized_pnl": realized_pnl,
            "realized_return": (
                # PR 4.1: matched_cost already includes the allocated buy
                # commission via unit_cost; adding buy_commission_allocated
                # again would double count the denominator.
                realized_pnl / matched_cost
                if matched_cost > 0
                else 0.0
            ),
            "industry_at_entry": holding.industry,
            "industry_at_exit": holding.industry,
            "matched_lot_ids": [lot.lot_id for lot in holding.lots],
        }
        trades.append(
            self._trade_proxy_sell(
                trade_date,
                symbol,
                quantity,
                raw_open,
                adj_open,
                commission,
                stamp_tax,
                reason,
                sale_result,
            )
        )
        del holdings[symbol]
        # Drain the ledger lots so realized-PnL aggregation in walk-forward
        # uses only the trades list; ledger cleanup is internal.
        for lot in list(ledger.lots(symbol)):
            ledger.sell(
                symbol=symbol,
                quantity=lot.quantity_remaining,
                sell_date=trade_date,
                sell_price=0.0,
                sell_commission=0.0,
                stamp_tax=0.0,
                industry_at_exit=holding.industry,
            )
        return True, cash

    # ------------------------------------------------------------------
    @staticmethod
    def _buy_block_reason(row: pd.Series, request: BacktestRequest) -> str | None:
        if bool(row.get("suspended", False)) or float(row.get("volume", 0)) <= 0:
            return "suspended"
        if pd.isna(row.get("open")) or float(row.get("open", 0)) <= 0:
            return "no_valid_open"
        if request.execution_policy == "daily_conservative" and bool(
            row.get("open_at_limit_up", False)
        ):
            return "open_at_limit_up"
        return None

    @staticmethod
    def _sell_block_reason(row: pd.Series, request: BacktestRequest) -> str | None:
        if bool(row.get("suspended", False)) or float(row.get("volume", 0)) <= 0:
            return "suspended"
        if pd.isna(row.get("open")) or float(row.get("open", 0)) <= 0:
            return "no_valid_open"
        if request.execution_policy == "daily_conservative" and bool(
            row.get("open_at_limit_down", False)
        ):
            return "open_at_limit_down"
        return None

    @staticmethod
    def _adj_open(row: pd.Series) -> float:
        value = row.get("adj_open")
        if value is None or pd.isna(value):
            return 0.0
        return float(value)

    def _market_value(self, day: pd.DataFrame, holdings: dict[str, _Holding]) -> float:
        return sum(self._market_value_of(day, holding) for holding in holdings.values())

    def _market_value_of(self, day: pd.DataFrame, holding: _Holding) -> float:
        adj_close = 0.0
        if holding.symbol in day.index:
            row = day.loc[holding.symbol]
            adj_close = self._adj_close(cast(pd.Series, row))
        # PR 4.1: fall back ONLY on the last causal-adjusted close.  The raw
        # close lives on a different scale after corporate actions and must
        # never feed proxy valuation.
        if adj_close <= 0:
            adj_close = holding.last_adj_close
        if adj_close <= 0:
            return 0.0
        return sum(
            proxy_market_value(
                ProxyLotRecord(
                    entry_raw_notional=lot.raw_notional,
                    entry_adj_open=lot.adj_open,
                    entry_raw_quantity=lot.quantity_remaining,
                ),
                adj_close,
            )
            for lot in holding.lots
        )

    @staticmethod
    def _adj_close(row: pd.Series) -> float:
        value = row.get("adj_close")
        if value is None or pd.isna(value):
            return 0.0
        parsed = float(value)
        # PR 6: Inf/-Inf must never reach proxy valuation (equity, returns
        # and drawdown would become Inf/NaN).  Non-finite or non-positive
        # values fall back to the last known adjusted close.
        return parsed if np.isfinite(parsed) and parsed > 0 else 0.0

    @staticmethod
    def _average_cost(holding: _Holding) -> float:
        total = sum(lot.quantity_cost(lot.quantity_remaining) for lot in holding.lots)
        quantity = holding.quantity
        return total / quantity if quantity > 0 else 0.0

    @staticmethod
    def _mark_positions(day: pd.DataFrame, holdings: dict[str, _Holding]) -> None:
        for holding in holdings.values():
            if holding.symbol not in day.index:
                continue
            row = cast(pd.Series, day.loc[holding.symbol])
            if pd.notna(row.get("close")):
                holding.last_raw_close = float(cast(Any, row["close"]))
            adj_close = AshareDailyV2ProxyEngine._adj_close(row)
            if adj_close > 0:
                holding.last_adj_close = adj_close

    @staticmethod
    def _targets_by_execution_date(
        signals: pd.DataFrame, trade_dates: list[Any], top_n: int
    ) -> dict[Any, list[str]]:
        next_date = {current: following for current, following in pairwise(trade_dates)}
        targets: dict[Any, list[str]] = {}
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
        # PR 5.3: proxy engine fails closed when the v2 execution fields are
        # missing.  adj_open/adj_close carry the causal total-return scale;
        # open_at_limit_* drive open-time tradability.  Never silently fall
        # back to raw prices (that re-introduces corporate-action distortion
        # and the close-time limit leakage class of bugs).
        required = {
            "date",
            "symbol",
            "open",
            "close",
            "volume",
            "adj_open",
            "adj_close",
            "open_at_limit_up",
            "open_at_limit_down",
        }
        missing = required.difference(market_data.columns)
        if missing:
            raise ValueError(
                "proxy market data is missing v2 execution columns: "
                f"{sorted(missing)}"
            )
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
        trade_date: Any,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        commission: float,
        stamp_tax: float,
        reason: str,
        *,
        lot_id: str = "",
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
            "lot_id": lot_id,
        }

    @staticmethod
    def _trade_proxy_sell(
        trade_date: Any,
        symbol: str,
        quantity: int,
        raw_open: float,
        adj_open: float,
        commission: float,
        stamp_tax: float,
        reason: str,
        sale_result: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "trade_date": trade_date.date().isoformat(),
            "symbol": symbol,
            "side": "sell",
            "quantity": quantity,
            "price": round(raw_open, 6),
            "adj_open": round(adj_open, 6),
            "gross_amount": round(sale_result["sell_gross"], 4),
            "commission": round(commission, 4),
            "stamp_tax": round(stamp_tax, 4),
            "reason": reason,
            **{
                key: round(value, 6) if isinstance(value, float) else value
                for key, value in sale_result.items()
            },
        }

    @staticmethod
    def _failure(trade_date: Any, symbol: str, reason: str, side: str) -> dict[str, Any]:
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

    def _metadata(self, request: BacktestRequest) -> dict[str, Any]:
        return {
            "engine": self.engine_code,
            "formal_ashare_result": False,
            "execution_result_level": "proxy",
            "corporate_action_mode": self.corporate_action_mode,
            "execution_confidence": self.execution_confidence,
            "execution_policy": request.execution_policy,
            "daily_approximation": True,
            "signal_timing": "after_close",
            "execution_timing": "next_trade_day_open",
            "production_eligible": False,
            "rules": [
                "T+1",
                "100_share_board_lot",
                "suspension",
                "no_valid_open",
                "open_at_limit_block_conservative",
                "commission_and_minimum",
                "stamp_tax_on_sell",
                "slippage",
                "total_return_proxy_corporate_actions",
            ],
        }

    def _empty_result(self, request: BacktestRequest) -> BacktestResult:
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
            metadata=self._metadata(request),
        )


def _frictionless(request: BacktestRequest) -> BacktestRequest:
    from dataclasses import replace

    return replace(
        request,
        commission_rate=0.0,
        minimum_commission=0.0,
        stamp_tax_rate=0.0,
        slippage_bps=0.0,
    )
