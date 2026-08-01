from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class SwingPointConfig:
    left_window: int = 3
    right_window: int = 3
    atr_filter: float = 0.5
    min_price_change: float = 0.02
    min_trading_day_interval: int = 3


class PriceActionAnalyzer:
    def __init__(self, config: SwingPointConfig | None = None) -> None:
        self.config = config or SwingPointConfig()

    def analyze(self, daily: pd.DataFrame) -> pd.DataFrame:
        frame = daily.copy()
        frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
        outputs = [
            self._analyze_symbol(group.copy())
            for _, group in frame.sort_values("date").groupby("symbol", sort=False)
        ]
        return pd.concat(outputs, ignore_index=True) if outputs else pd.DataFrame()

    def _analyze_symbol(self, group: pd.DataFrame) -> pd.DataFrame:
        group = group.reset_index(drop=True)
        high = group["high"].astype(float)
        low = group["low"].astype(float)
        close = group["close"].astype(float)
        open_ = group["open"].astype(float)
        previous_close = close.shift()
        tr = pd.concat(
            [(high - low), (high - previous_close).abs(), (low - previous_close).abs()],
            axis=1,
        ).max(axis=1)
        atr = tr.rolling(14, min_periods=5).mean()
        left = self.config.left_window
        right = self.config.right_window
        raw_swing_high = high.eq(high.rolling(left + right + 1, center=True).max())
        raw_swing_low = low.eq(low.rolling(left + right + 1, center=True).min())
        pivot_high = self._filter_swing_points(high, raw_swing_high, atr)
        pivot_low = self._filter_swing_points(low, raw_swing_low, atr)
        # A centred fractal is only knowable after `right_window` later bars.
        # Move both the event and its pivot value to that confirmation date.
        confirmed_high_price = high.where(pivot_high).shift(right)
        confirmed_low_price = low.where(pivot_low).shift(right)
        group["swing_high"] = confirmed_high_price.notna()
        group["swing_low"] = confirmed_low_price.notna()
        group["swing_high_price"] = confirmed_high_price
        group["swing_low_price"] = confirmed_low_price
        group["swing_high_pivot_date"] = group["date"].where(pivot_high).shift(right)
        group["swing_low_pivot_date"] = group["date"].where(pivot_low).shift(right)
        high_relation = self._confirmed_relation_state(confirmed_high_price)
        low_relation = self._confirmed_relation_state(confirmed_low_price)
        group["higher_high"] = high_relation.eq(1).astype(float)
        group["lower_high"] = high_relation.eq(-1).astype(float)
        group["higher_low"] = low_relation.eq(1).astype(float)
        group["lower_low"] = low_relation.eq(-1).astype(float)
        group["inside_bar"] = ((high < high.shift()) & (low > low.shift())).astype(float)
        group["outside_bar"] = ((high > high.shift()) & (low < low.shift())).astype(float)
        prior_high20 = high.shift().rolling(20, min_periods=10).max()
        group["breakout"] = (close > prior_high20).astype(float)
        group["failed_breakout"] = ((high > prior_high20) & (close <= prior_high20)).astype(float)
        day_range = high - low
        group["range_expansion"] = (
            day_range > day_range.shift().rolling(20, min_periods=5).median() * 1.5
        ).astype(float)
        group["volatility_contraction"] = (
            close.pct_change(fill_method=None).rolling(10, min_periods=5).std()
            < close.pct_change(fill_method=None).rolling(30, min_periods=10).std() * 0.7
        ).astype(float)
        group["gap"] = ((open_ - previous_close).abs() > atr * 0.5).astype(float)
        ma20 = close.rolling(20, min_periods=5).mean()
        ma60 = close.rolling(60, min_periods=20).mean()
        distance_atr = (close - ma20).abs() / atr.replace(0, np.nan)
        high_volume_stall = (
            group["volume"] > group["volume"].rolling(20, min_periods=5).mean() * 1.8
        ) & (close < high - day_range * 0.35)
        score = (
            group["higher_low"] * 2
            + group["higher_high"]
            + group["breakout"] * 2
            + ((close > prior_high20) & (close.shift() > prior_high20.shift())).astype(float)
            + ((ma20.diff() > 0) & (ma60.diff() > 0)).astype(float)
            + (distance_atr <= 2.5).astype(float)
            + (group["volatility_contraction"].shift().fillna(0) * group["range_expansion"])
            - group["failed_breakout"] * 3
            - (distance_atr > 4).astype(float) * 3
            - group["lower_high"] * 2
            - high_volume_stall.astype(float) * 3
        )
        group["pa_score"] = score.clip(lower=0, upper=10)
        group["distance_from_ma20_atr"] = distance_atr
        group["high_volume_stall"] = high_volume_stall.astype(float)
        return group

    @staticmethod
    def _confirmed_relation_state(confirmed_prices: pd.Series) -> pd.Series:
        previous = confirmed_prices.where(confirmed_prices.notna()).shift().ffill()
        event = pd.Series(np.nan, index=confirmed_prices.index, dtype=float)
        comparable = confirmed_prices.notna() & previous.notna()
        event.loc[comparable] = np.sign(confirmed_prices.loc[comparable] - previous.loc[comparable])
        return event.ffill().fillna(0.0)

    def _filter_swing_points(
        self,
        prices: pd.Series,
        raw_mask: pd.Series,
        atr: pd.Series,
    ) -> pd.Series:
        accepted = pd.Series(False, index=prices.index)
        last_position: int | None = None
        last_price: float | None = None
        for position in range(len(prices)):
            if not bool(raw_mask.iloc[position]):
                continue
            price = float(prices.iloc[position])
            current_atr = float(atr.iloc[position]) if pd.notna(atr.iloc[position]) else 0.0
            if last_position is None or last_price is None:
                accepted.iloc[position] = True
                last_position = position
                last_price = price
                continue
            minimum_move = max(
                abs(last_price) * self.config.min_price_change,
                current_atr * self.config.atr_filter,
            )
            enough_time = position - last_position >= self.config.min_trading_day_interval
            enough_move = abs(price - last_price) >= minimum_move
            if enough_time and enough_move:
                accepted.iloc[position] = True
                last_position = position
                last_price = price
        return accepted
