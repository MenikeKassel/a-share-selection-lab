"""FIFO lot ledger with realized PnL (PR 4).

The execution engine consumes buys and sells through this ledger so every
sell can emit an exact realized PnL with matched lots, allocated buy
commission, industry at entry and matched quantity.  Walk-forward must not
re-derive pairings from the raw trade list anymore.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PositionLot:
    lot_id: str
    symbol: str
    entry_date: Any  # pd.Timestamp
    quantity_remaining: int
    entry_price: float
    allocated_buy_commission: float
    unit_cost: float  # (gross + allocated commission) / quantity
    industry_at_entry: str
    raw_notional: float = 0.0  # proxy mode: entry_raw_notional
    adj_open: float = 0.0  # proxy mode: causal adj open at entry

    def quantity_cost(self, quantity: int) -> float:
        return self.unit_cost * quantity


@dataclass(slots=True)
class SaleMatch:
    lot_id: str
    matched_quantity: int
    matched_cost: float  # cost basis of the matched quantity
    allocated_buy_commission: float
    entry_price: float
    entry_date: Any
    industry_at_entry: str


@dataclass(slots=True)
class SaleResult:
    symbol: str
    sell_date: Any
    matched_quantity: int
    matched_cost: float
    sell_gross: float
    buy_commission_allocated: float
    sell_commission: float
    stamp_tax: float
    realized_pnl: float
    realized_return: float
    industry_at_entry: str
    industry_at_exit: str
    matched_lot_ids: list[str] = field(default_factory=list)


class LotLedger:
    """FIFO per-symbol lot store."""

    def __init__(self) -> None:
        self._lots: dict[str, list[PositionLot]] = {}
        self._next_id = 0

    def _new_lot_id(self) -> str:
        self._next_id += 1
        return f"L{self._next_id:06d}"

    def add_lot(self, lot: PositionLot) -> PositionLot:
        if lot.lot_id == "":
            lot.lot_id = self._new_lot_id()
        self._lots.setdefault(lot.symbol, []).append(lot)
        return lot

    def open_quantity(self, symbol: str) -> int:
        return sum(lot.quantity_remaining for lot in self._lots.get(symbol, []))

    def lots(self, symbol: str) -> list[PositionLot]:
        return list(self._lots.get(symbol, []))

    def all_lots(self) -> Iterator[PositionLot]:
        for lots in self._lots.values():
            yield from lots

    def apply_share_multiplier(self, symbol: str, multiplier: float) -> None:
        """Adjust lot quantities/costs after bonus/capitalization/split.

        Cost basis is preserved: total cost stays constant, unit_cost
        shrinks inversely with the multiplier.  Only explicit events call
        this; adj_factor never does.
        """
        if multiplier <= 0 or multiplier == 1.0:
            return
        for lot in self._lots.get(symbol, []):
            new_quantity = int(lot.quantity_remaining * multiplier)
            if new_quantity <= 0:
                continue
            lot.unit_cost = lot.quantity_cost(lot.quantity_remaining) / new_quantity
            lot.quantity_remaining = new_quantity

    def sell(
        self,
        *,
        symbol: str,
        quantity: int,
        sell_date: Any,
        sell_price: float,
        sell_commission: float,
        stamp_tax: float,
        industry_at_exit: str,
    ) -> SaleResult | None:
        """Match a sell FIFO across lots; returns realized PnL or None."""
        lots = self._lots.get(symbol)
        if not lots or quantity <= 0:
            return None
        remaining = quantity
        matches: list[SaleMatch] = []
        for lot in lots:
            if remaining <= 0:
                break
            take = min(lot.quantity_remaining, remaining)
            if take <= 0:
                continue
            matches.append(
                SaleMatch(
                    lot_id=lot.lot_id,
                    matched_quantity=take,
                    matched_cost=lot.quantity_cost(take),
                    allocated_buy_commission=(
                        lot.allocated_buy_commission * take / lot.quantity_remaining
                        if lot.quantity_remaining > 0
                        else 0.0
                    ),
                    entry_price=lot.entry_price,
                    entry_date=lot.entry_date,
                    industry_at_entry=lot.industry_at_entry,
                )
            )
            lot.quantity_remaining -= take
            remaining -= take
        if remaining > 0:
            # Cannot match fully; restore consumed quantities.
            for match in matches:
                for lot in lots:
                    if lot.lot_id == match.lot_id:
                        lot.quantity_remaining += match.matched_quantity
                        break
            return None
        self._lots[symbol] = [lot for lot in lots if lot.quantity_remaining > 0]
        if not self._lots[symbol]:
            del self._lots[symbol]
        matched_quantity = sum(match.matched_quantity for match in matches)
        matched_cost = sum(match.matched_cost for match in matches)
        buy_commission_allocated = sum(
            match.allocated_buy_commission for match in matches
        )
        total_buy_commission = buy_commission_allocated
        sell_gross = matched_quantity * sell_price
        realized_pnl = sell_gross - matched_cost - sell_commission - stamp_tax
        return SaleResult(
            symbol=symbol,
            sell_date=sell_date,
            matched_quantity=matched_quantity,
            matched_cost=matched_cost,
            sell_gross=sell_gross,
            buy_commission_allocated=total_buy_commission,
            sell_commission=sell_commission,
            stamp_tax=stamp_tax,
            realized_pnl=realized_pnl,
            realized_return=(
                realized_pnl / (matched_cost + total_buy_commission)
                if matched_cost + total_buy_commission > 0
                else 0.0
            ),
            industry_at_entry=matches[0].industry_at_entry if matches else "unknown",
            industry_at_exit=industry_at_exit,
            matched_lot_ids=[match.lot_id for match in matches],
        )
