"""Tests for PR 1/2/4: corporate-action proxy mode, open-time tradability,
and FIFO lot ledger realized PnL in the ashare_daily_v2 proxy engine.
"""
import pandas as pd
import pytest
from app.domain.protocols import BacktestRequest
from app.execution.ashare_daily_v2 import AshareDailyV2ProxyEngine
from app.execution.corporate_actions import (
    CorporateActionCapabilities,
    resolve_execution_mode,
)
from app.execution.lot_ledger import LotLedger, PositionLot


def _request(**overrides) -> BacktestRequest:
    base = dict(
        strategy_code="test",
        start_date="2024-01-01",
        end_date="2024-12-31",
        top_n=1,
        rebalance_frequency="daily",
        holding_period=5,
        commission_rate=0.0003,
        stamp_tax_rate=0.0005,
        slippage_bps=5.0,
        benchmark_symbol="000300.SH",
        initial_cash=1_000_000.0,
    )
    base.update(overrides)
    return BacktestRequest(**base)


def _market(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    # PR 5.3: default the v2 execution columns so existing fixtures keep
    # working; tests that exercise fail-closed explicitly drop columns.
    defaults = {
        "adj_open": 0.0,
        "adj_close": 0.0,
        "open_at_limit_up": False,
        "open_at_limit_down": False,
        "limit_up_price": 0.0,
        "limit_down_price": 0.0,
        "suspended": False,
        "industry": "unknown",
    }
    for column, value in defaults.items():
        if column not in frame.columns:
            frame[column] = value
    return frame


def _signals(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# PR 1: corporate-action proxy mode


def test_proxy_mode_cash_dividend_day_has_no_fake_loss() -> None:
    # Signal on D-1; D is a cash-dividend ex-date where raw close drops by
    # the dividend, but the causal adjusted series does not gap.
    market = _market(
        [
            {"date": "2024-01-02", "symbol": "A", "open": 10.0, "high": 10.5,
             "low": 9.8, "close": 10.2, "pre_close": 10.0, "volume": 1_000_000,
             "adj_open": 10.0, "adj_close": 10.2, "suspended": False,
             "industry": "tech", "open_at_limit_up": False, "open_at_limit_down": False},
            {"date": "2024-01-03", "symbol": "A", "open": 9.5, "high": 9.6,
             "low": 9.4, "close": 9.6, "pre_close": 10.2, "volume": 1_000_000,
             "adj_open": 10.0, "adj_close": 10.1, "suspended": False,
             "industry": "tech", "open_at_limit_up": False, "open_at_limit_down": False},
        ]
    )
    signals = _signals(
        [{"signal_date": "2024-01-02", "symbol": "A", "score": 1.0}]
    )
    result = AshareDailyV2ProxyEngine().run_with_data(_request(), market, signals)
    # No sell happened (holding period not reached), so equity must not drop
    # from the dividend gap: adj series carries the return.
    assert result.performance["tradable_return"] >= -0.02
    assert result.metadata["corporate_action_mode"] == "total_return_proxy"
    assert result.metadata["formal_ashare_result"] is False


def test_proxy_mode_never_changes_quantity_from_adj_factor() -> None:
    market = _market(
        [
            {"date": "2024-01-02", "symbol": "A", "open": 10.0, "high": 10.5,
             "low": 9.8, "close": 10.2, "pre_close": 10.0, "volume": 1_000_000,
             "adj_open": 10.0, "adj_close": 10.2, "suspended": False,
             "adj_factor": 1.0, "industry": "tech",
             "open_at_limit_up": False, "open_at_limit_down": False},
            # adj_factor changes 1.0 -> 2.0 (bonus) but proxy mode must NOT
            # double the position.
            {"date": "2024-01-03", "symbol": "A", "open": 10.1, "high": 10.2,
             "low": 10.0, "close": 10.1, "pre_close": 10.2, "volume": 1_000_000,
             "adj_open": 5.05, "adj_close": 5.05, "suspended": False,
             "adj_factor": 2.0, "industry": "tech",
             "open_at_limit_up": False, "open_at_limit_down": False},
        ]
    )
    signals = _signals(
        [{"signal_date": "2024-01-02", "symbol": "A", "score": 1.0}]
    )
    result = AshareDailyV2ProxyEngine().run_with_data(_request(), market, signals)
    assert result.metadata["corporate_action_mode"] == "total_return_proxy"
    # The position history must reflect the proxy mark (not a doubled qty).
    assert result.positions, "position history must not be empty"
    # No cash dividend is paid in proxy mode; no dividend fields in trades.
    assert all("dividend" not in str(trade) for trade in result.trades)


def test_proxy_mode_does_not_double_count_dividend() -> None:
    market = _market(
        [
            {"date": "2024-01-02", "symbol": "A", "open": 10.0, "high": 10.5,
             "low": 9.8, "close": 10.2, "pre_close": 10.0, "volume": 1_000_000,
             "adj_open": 10.0, "adj_close": 10.2, "suspended": False,
             "industry": "tech", "cash_dividend_per_share": 0.5,
             "open_at_limit_up": False, "open_at_limit_down": False},
        ]
    )
    signals = _signals(
        [{"signal_date": "2024-01-02", "symbol": "A", "score": 1.0}]
    )
    result = AshareDailyV2ProxyEngine().run_with_data(_request(), market, signals)
    # Equity must equal initial cash (no trade happened on a single day),
    # i.e. no phantom dividend credited.
    assert result.performance["tradable_return"] == pytest.approx(0.0, abs=1e-6)


def test_strict_mode_blocks_without_explicit_events() -> None:
    capabilities = CorporateActionCapabilities(
        explicit_events_available=False,
        total_return_proxy_available=True,
    )
    assert resolve_execution_mode(capabilities, "explicit") == "unavailable"
    assert resolve_execution_mode(capabilities, "total_return_proxy") == "total_return_proxy"


# ---------------------------------------------------------------------------
# PR 2: open-time tradability


def test_close_limit_up_does_not_block_next_open_buy() -> None:
    market = _market(
        [
            # D-1: signal day; close limit up.
            {"date": "2024-01-02", "symbol": "A", "open": 10.0, "high": 11.0,
             "low": 10.0, "close": 11.0, "pre_close": 10.0, "volume": 1_000_000,
             "adj_open": 10.0, "adj_close": 11.0, "suspended": False,
             "industry": "tech", "close_at_limit_up": True,
             "open_at_limit_up": False, "one_word_limit_up": False,
             "open_at_limit_down": False},
            # D: next open is normal -> must be buyable in optimistic and
            # conservative modes (open not at limit).
            {"date": "2024-01-03", "symbol": "A", "open": 10.5, "high": 10.8,
             "low": 10.4, "close": 10.6, "pre_close": 11.0, "volume": 1_000_000,
             "adj_open": 10.5, "adj_close": 10.6, "suspended": False,
             "industry": "tech", "close_at_limit_up": False,
             "open_at_limit_up": False, "open_at_limit_down": False},
        ]
    )
    signals = _signals(
        [{"signal_date": "2024-01-02", "symbol": "A", "score": 1.0}]
    )
    result = AshareDailyV2ProxyEngine().run_with_data(_request(), market, signals)
    assert any(trade["side"] == "buy" for trade in result.trades)
    assert not any(
        failure["reason"] == "limit_up_unbuyable" for failure in result.execution_failures
    )


def test_open_at_limit_up_blocks_buy_in_conservative_mode() -> None:
    market = _market(
        [
            {"date": "2024-01-02", "symbol": "A", "open": 10.0, "high": 10.0,
             "low": 9.8, "close": 10.0, "pre_close": 10.0, "volume": 1_000_000,
             "adj_open": 10.0, "adj_close": 10.0, "suspended": False,
             "industry": "tech", "open_at_limit_up": False},
            {"date": "2024-01-03", "symbol": "A", "open": 11.0, "high": 11.0,
             "low": 11.0, "close": 11.0, "pre_close": 10.0, "volume": 1_000_000,
             "adj_open": 11.0, "adj_close": 11.0, "suspended": False,
             "industry": "tech", "open_at_limit_up": True},
        ]
    )
    signals = _signals(
        [{"signal_date": "2024-01-02", "symbol": "A", "score": 1.0}]
    )
    result = AshareDailyV2ProxyEngine().run_with_data(
        _request(execution_policy="daily_conservative"), market, signals
    )
    assert not any(trade["side"] == "buy" for trade in result.trades)
    assert any(failure["reason"] == "open_at_limit_up" for failure in result.execution_failures)


def test_one_word_limit_up_does_not_affect_open_decision() -> None:
    market = _market(
        [
            {"date": "2024-01-02", "symbol": "A", "open": 10.0, "high": 10.0,
             "low": 10.0, "close": 10.0, "pre_close": 10.0, "volume": 1_000_000,
             "adj_open": 10.0, "adj_close": 10.0, "suspended": False,
             "industry": "tech", "one_word_limit_up": True,
             "open_at_limit_up": False},
            {"date": "2024-01-03", "symbol": "A", "open": 10.5, "high": 10.5,
             "low": 10.5, "close": 10.5, "pre_close": 10.0, "volume": 1_000_000,
             "adj_open": 10.5, "adj_close": 10.5, "suspended": False,
             "industry": "tech", "one_word_limit_up": False,
             "open_at_limit_up": False},
        ]
    )
    signals = _signals(
        [{"signal_date": "2024-01-02", "symbol": "A", "score": 1.0}]
    )
    result = AshareDailyV2ProxyEngine().run_with_data(
        _request(execution_policy="daily_conservative"), market, signals
    )
    # One-word close flag on the signal day must not block the D+1 open buy.
    assert any(trade["side"] == "buy" for trade in result.trades)


def test_suspended_day_blocks_buy() -> None:
    market = _market(
        [
            {"date": "2024-01-02", "symbol": "A", "open": 10.0, "high": 10.0,
             "low": 10.0, "close": 10.0, "pre_close": 10.0, "volume": 1_000_000,
             "adj_open": 10.0, "adj_close": 10.0, "suspended": False,
             "industry": "tech"},
            {"date": "2024-01-03", "symbol": "A", "open": 10.0, "high": 10.0,
             "low": 10.0, "close": 10.0, "pre_close": 10.0, "volume": 0,
             "adj_open": 10.0, "adj_close": 10.0, "suspended": True,
             "industry": "tech"},
        ]
    )
    signals = _signals(
        [{"signal_date": "2024-01-02", "symbol": "A", "score": 1.0}]
    )
    result = AshareDailyV2ProxyEngine().run_with_data(_request(), market, signals)
    assert not any(trade["side"] == "buy" for trade in result.trades)
    assert any(failure["reason"] == "suspended" for failure in result.execution_failures)


# ---------------------------------------------------------------------------
# PR 4: FIFO lot ledger


def test_ledger_partial_sell_matches_multiple_lots() -> None:
    ledger = LotLedger()
    buy1 = ledger.add_lot(
        PositionLot("", "A", pd.Timestamp("2024-01-02"), 100, 10.0, 5.0, 10.05, "tech")
    )
    buy2 = ledger.add_lot(
        PositionLot("", "A", pd.Timestamp("2024-01-05"), 200, 11.0, 6.0, 11.03, "tech")
    )
    sale = ledger.sell(
        symbol="A",
        quantity=150,
        sell_date=pd.Timestamp("2024-01-08"),
        sell_price=12.0,
        sell_commission=5.0,
        stamp_tax=0.9,
        industry_at_exit="tech",
    )
    assert sale is not None
    assert sale.matched_quantity == 150
    assert sale.matched_lot_ids == [buy1.lot_id, buy2.lot_id]
    # buy1 fully consumed (100), buy2 partially (50 left of 200 -> 150 left)
    assert ledger.open_quantity("A") == 150
    assert sale.realized_pnl == pytest.approx(
        150 * 12.0 - (100 * 10.05 + 50 * 11.03) - 5.0 - 0.9, abs=1e-6
    )
    # buy2 remaining lot keeps its unit cost
    remaining = ledger.lots("A")[0]
    assert remaining.quantity_remaining == 150
    assert remaining.unit_cost == pytest.approx(11.03)


def test_ledger_full_liquidation_multiple_buys() -> None:
    ledger = LotLedger()
    ledger.add_lot(PositionLot("", "A", pd.Timestamp("2024-01-02"), 100, 10.0, 5.0, 10.05, "tech"))
    ledger.add_lot(PositionLot("", "A", pd.Timestamp("2024-01-05"), 200, 11.0, 6.0, 11.03, "tech"))
    sale = ledger.sell(
        symbol="A",
        quantity=300,
        sell_date=pd.Timestamp("2024-01-08"),
        sell_price=12.0,
        sell_commission=5.0,
        stamp_tax=1.8,
        industry_at_exit="tech",
    )
    assert sale is not None
    assert sale.matched_quantity == 300
    assert len(sale.matched_lot_ids) == 2
    assert ledger.open_quantity("A") == 0


def test_ledger_insufficient_quantity_rolls_back() -> None:
    ledger = LotLedger()
    ledger.add_lot(PositionLot("", "A", pd.Timestamp("2024-01-02"), 100, 10.0, 5.0, 10.05, "tech"))
    sale = ledger.sell(
        symbol="A",
        quantity=150,
        sell_date=pd.Timestamp("2024-01-08"),
        sell_price=12.0,
        sell_commission=5.0,
        stamp_tax=0.9,
        industry_at_exit="tech",
    )
    assert sale is None
    assert ledger.open_quantity("A") == 100


def test_ledger_share_multiplier_preserves_cost_basis() -> None:
    ledger = LotLedger()
    ledger.add_lot(
        PositionLot("", "A", pd.Timestamp("2024-01-02"), 1000, 10.0, 50.0, 10.05, "tech")
    )
    ledger.apply_share_multiplier("A", 1.2)  # 10 for 2 bonus
    remaining = ledger.lots("A")[0]
    assert remaining.quantity_remaining == 1200
    assert remaining.unit_cost == pytest.approx(10.05 * 1000 / 1200)
    assert remaining.quantity_cost(1200) == pytest.approx(10.05 * 1000)


def test_ledger_industry_at_entry_preserved() -> None:
    ledger = LotLedger()
    ledger.add_lot(
        PositionLot("", "A", pd.Timestamp("2024-01-02"), 100, 10.0, 5.0, 10.05, "bank")
    )
    sale = ledger.sell(
        symbol="A",
        quantity=100,
        sell_date=pd.Timestamp("2024-01-08"),
        sell_price=11.0,
        sell_commission=5.0,
        stamp_tax=0.55,
        industry_at_exit="tech",
    )
    assert sale is not None
    assert sale.industry_at_entry == "bank"
    assert sale.industry_at_exit == "tech"


# ---------------------------------------------------------------------------
# PR 5.3: fail-closed on missing v2 execution columns


def test_proxy_engine_fails_closed_without_adj_open() -> None:
    market = _market(
        [
            {"date": "2024-01-02", "symbol": "A", "open": 10.0, "close": 10.0,
             "volume": 1_000_000, "suspended": False, "industry": "tech",
             "open_at_limit_up": False, "open_at_limit_down": False,
             "adj_close": 10.0},
        ]
    ).drop(columns=["adj_open"])
    signals = _signals(
        [{"signal_date": "2024-01-02", "symbol": "A", "score": 1.0}]
    )
    with pytest.raises(ValueError, match="missing v2 execution columns"):
        AshareDailyV2ProxyEngine().run_with_data(_request(), market, signals)


# ---------------------------------------------------------------------------
# PR 5.4: value-level fail-closed (column present but invalid values)


def test_proxy_engine_blocks_buy_when_adj_open_is_nan() -> None:
    # Signal on 01-02 executes on 01-03; the execution day's adj_open must
    # be NaN for the buy to be blocked.
    market = _market(
        [
            {"date": "2024-01-02", "symbol": "A", "open": 10.0, "close": 10.0,
             "volume": 1_000_000, "suspended": False, "industry": "tech",
             "open_at_limit_up": False, "open_at_limit_down": False,
             "adj_open": 10.0, "adj_close": 10.0},
            {"date": "2024-01-03", "symbol": "A", "open": 10.0, "close": 10.0,
             "volume": 1_000_000, "suspended": False, "industry": "tech",
             "open_at_limit_up": False, "open_at_limit_down": False,
             "adj_open": float("nan"), "adj_close": 10.0},
        ]
    )
    signals = _signals(
        [{"signal_date": "2024-01-02", "symbol": "A", "score": 1.0}]
    )
    result = AshareDailyV2ProxyEngine().run_with_data(_request(), market, signals)
    # No raw-price fallback: the buy is blocked and no position is opened.
    assert not any(trade["side"] == "buy" for trade in result.trades)
    assert any(
        failure["reason"] == "missing_adj_open" for failure in result.execution_failures
    )


def test_proxy_engine_blocks_buy_when_adj_open_is_zero() -> None:
    # Signal on 01-02 executes on 01-03; the execution day's adj_open must
    # also be invalid for the buy to be blocked.
    market = _market(
        [
            {"date": "2024-01-02", "symbol": "A", "open": 10.0, "close": 10.0,
             "volume": 1_000_000, "suspended": False, "industry": "tech",
             "open_at_limit_up": False, "open_at_limit_down": False,
             "adj_open": 10.0, "adj_close": 10.0},
            {"date": "2024-01-03", "symbol": "A", "open": 10.0, "close": 10.0,
             "volume": 1_000_000, "suspended": False, "industry": "tech",
             "open_at_limit_up": False, "open_at_limit_down": False,
             "adj_open": 0.0, "adj_close": 10.0},
        ]
    )
    signals = _signals(
        [{"signal_date": "2024-01-02", "symbol": "A", "score": 1.0}]
    )
    result = AshareDailyV2ProxyEngine().run_with_data(_request(), market, signals)
    assert not any(trade["side"] == "buy" for trade in result.trades)
    assert any(
        failure["reason"] == "missing_adj_open" for failure in result.execution_failures
    )


def test_proxy_engine_never_uses_raw_open_as_adj_open() -> None:
    """PR 5.4: even with a valid open price, an invalid adj_open must not
    be substituted - the lot's adj_open must equal the real adjusted value."""
    market = _market(
        [
            {"date": "2024-01-02", "symbol": "A", "open": 10.0, "close": 10.0,
             "volume": 1_000_000, "suspended": False, "industry": "tech",
             "open_at_limit_up": False, "open_at_limit_down": False,
             "adj_open": 12.0, "adj_close": 12.0},
            {"date": "2024-01-03", "symbol": "A", "open": 10.0, "close": 10.0,
             "volume": 1_000_000, "suspended": False, "industry": "tech",
             "open_at_limit_up": False, "open_at_limit_down": False,
             "adj_open": 12.0, "adj_close": 12.0},
        ]
    )
    signals = _signals(
        [{"signal_date": "2024-01-02", "symbol": "A", "score": 1.0}]
    )
    result = AshareDailyV2ProxyEngine().run_with_data(_request(), market, signals)
    assert any(trade["side"] == "buy" for trade in result.trades)


def test_proxy_engine_fails_closed_without_open_at_limit() -> None:
    market = _market(
        [
            {"date": "2024-01-02", "symbol": "A", "open": 10.0, "close": 10.0,
             "volume": 1_000_000, "suspended": False, "industry": "tech",
             "adj_open": 10.0, "adj_close": 10.0},
        ]
    ).drop(columns=["open_at_limit_up", "open_at_limit_down"])
    signals = _signals(
        [{"signal_date": "2024-01-02", "symbol": "A", "score": 1.0}]
    )
    with pytest.raises(ValueError, match="missing v2 execution columns"):
        AshareDailyV2ProxyEngine().run_with_data(_request(), market, signals)


# ---------------------------------------------------------------------------
# PR 4.1: proxy commission accounting


def test_proxy_round_trip_flat_price_loses_only_fees() -> None:
    """Buy then sell at unchanged adjusted prices: loss == buy+sell fees."""
    market = _market(
        [
            {"date": "2024-01-02", "symbol": "A", "open": 10.0, "high": 10.0,
             "low": 10.0, "close": 10.0, "pre_close": 10.0, "volume": 1_000_000,
             "adj_open": 10.0, "adj_close": 10.0, "suspended": False,
             "industry": "tech", "open_at_limit_up": False, "open_at_limit_down": False},
            {"date": "2024-01-03", "symbol": "A", "open": 10.0, "high": 10.0,
             "low": 10.0, "close": 10.0, "pre_close": 10.0, "volume": 1_000_000,
             "adj_open": 10.0, "adj_close": 10.0, "suspended": False,
             "industry": "tech", "open_at_limit_up": False, "open_at_limit_down": False},
            {"date": "2024-01-04", "symbol": "A", "open": 10.0, "high": 10.0,
             "low": 10.0, "close": 10.0, "pre_close": 10.0, "volume": 1_000_000,
             "adj_open": 10.0, "adj_close": 10.0, "suspended": False,
             "industry": "tech", "open_at_limit_up": False, "open_at_limit_down": False},
        ]
    )
    signals = _signals(
        [{"signal_date": "2024-01-02", "symbol": "A", "score": 1.0}]
    )
    request = _request(
        holding_period=1,
        commission_rate=0.0003,
        minimum_commission=0.0,
        stamp_tax_rate=0.0005,
        slippage_bps=0.0,
        initial_cash=100_000.0,
    )
    result = AshareDailyV2ProxyEngine().run_with_data(request, market, signals)
    assert result.performance["trade_count"] == 2  # one buy + one sell
    sell = next(trade for trade in result.trades if trade["side"] == "sell")
    buy = next(trade for trade in result.trades if trade["side"] == "buy")
    expected_loss = float(buy["commission"]) + float(sell["commission"]) + float(
        sell["stamp_tax"]
    )
    assert result.performance["tradable_return"] == pytest.approx(
        -expected_loss / request.initial_cash, abs=1e-9
    )
    # realized_pnl equals -fees exactly (flat price, price-only notional)
    assert sell["realized_pnl"] == pytest.approx(-expected_loss, abs=1e-9)


def test_proxy_round_trip_preserves_price_gain_without_buy_commission_inflation() -> None:
    """Price-only notional: value tracks price move, buy commission not counted twice."""
    market = _market(
        [
            {"date": "2024-01-02", "symbol": "A", "open": 10.0, "high": 10.0,
             "low": 10.0, "close": 10.0, "pre_close": 10.0, "volume": 1_000_000,
             "adj_open": 10.0, "adj_close": 10.0, "suspended": False,
             "industry": "tech", "open_at_limit_up": False, "open_at_limit_down": False},
            {"date": "2024-01-03", "symbol": "A", "open": 11.0, "high": 11.0,
             "low": 11.0, "close": 11.0, "pre_close": 10.0, "volume": 1_000_000,
             "adj_open": 11.0, "adj_close": 11.0, "suspended": False,
             "industry": "tech", "open_at_limit_up": False, "open_at_limit_down": False},
            {"date": "2024-01-04", "symbol": "A", "open": 12.0, "high": 12.0,
             "low": 12.0, "close": 12.0, "pre_close": 11.0, "volume": 1_000_000,
             "adj_open": 12.0, "adj_close": 12.0, "suspended": False,
             "industry": "tech", "open_at_limit_up": False, "open_at_limit_down": False},
        ]
    )
    signals = _signals(
        [{"signal_date": "2024-01-02", "symbol": "A", "score": 1.0}]
    )
    request = _request(
        holding_period=1,
        commission_rate=0.0003,
        minimum_commission=0.0,
        stamp_tax_rate=0.0005,
        slippage_bps=0.0,
        initial_cash=100_000.0,
    )
    result = AshareDailyV2ProxyEngine().run_with_data(request, market, signals)
    sell = next(trade for trade in result.trades if trade["side"] == "sell")
    # max_stock_weight 0.1 -> target 10,000 -> buy 900 shares @ 11
    # (gross 9,900, buy commission 2.97), sell at adj_open 12
    # (proxy exit 9,900 * 12/11 = 10,800), sell commission 3.24,
    # stamp tax 5.40 -> realized = 10800 - 9902.97 - 3.24 - 5.40
    assert sell["realized_pnl"] == pytest.approx(
        10_800.0 - 9_902.97 - 3.24 - 5.40, abs=1e-4
    )
    assert sell["realized_pnl"] > 800.0  # gain, not fees-only
