from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, cast

import numpy as np
import pandas as pd

from app.data.contracts import MINUTE_REQUIRED_COLUMNS, UNAVAILABLE_MICROSTRUCTURE


class VolumeUnitMismatchError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MinuteConfirmationResult:
    status: str
    minute_score: float | None
    vwap: dict[str, float | None]
    opening_ranges: dict[str, dict[str, Any]]
    relative_volume: dict[str, float | None]
    volume_profile: dict[str, Any]
    tpo_profile: dict[str, Any]
    closing_strength: dict[str, float | bool | None]
    unavailable_microstructure: dict[str, str]
    data_confidence: str


class MinuteConfirmationAnalyzer:
    def analyze_for_date(
        self,
        minute_data: pd.DataFrame,
        selection_date: date,
        *,
        breakout_price: float | None = None,
        volume_unit: str = "shares",
    ) -> MinuteConfirmationResult:
        """Separate the selected session from prior sessions before analysis."""

        if missing := MINUTE_REQUIRED_COLUMNS.difference(minute_data.columns):
            raise ValueError(f"minute data is missing columns: {sorted(missing)}")
        frame = minute_data.copy()
        frame["timestamp"] = pd.to_datetime(frame["timestamp"])
        target = pd.Timestamp(selection_date)
        normalized = frame["timestamp"].dt.normalize()
        current = frame.loc[normalized == target]
        history = frame.loc[normalized < target]
        if current.empty:
            return self.unavailable()
        return self.analyze(
            current,
            historical_minutes=history,
            breakout_price=breakout_price,
            volume_unit=volume_unit,
        )

    def analyze(
        self,
        minute_data: pd.DataFrame,
        *,
        historical_minutes: pd.DataFrame | None = None,
        breakout_price: float | None = None,
        volume_unit: str = "shares",
    ) -> MinuteConfirmationResult:
        if missing := MINUTE_REQUIRED_COLUMNS.difference(minute_data.columns):
            raise ValueError(f"minute data is missing columns: {sorted(missing)}")
        frame = minute_data.copy()
        frame["timestamp"] = pd.to_datetime(frame["timestamp"])
        frame = frame.sort_values("timestamp").drop_duplicates("timestamp")
        if not self._complete_session(frame):
            return self.unavailable()
        multiplier = {"shares": 1.0, "lots": 100.0}.get(volume_unit)
        if multiplier is None:
            raise ValueError("volume_unit must be shares or lots")
        effective_volume = pd.to_numeric(frame["volume"], errors="coerce") * multiplier
        amount = pd.to_numeric(frame["amount"], errors="coerce")
        cumulative_volume = effective_volume.cumsum().replace(0, np.nan)
        cumulative_vwap = amount.cumsum() / cumulative_volume
        full_day_vwap = float(amount.sum() / effective_volume.sum())
        observed_low = float(frame["low"].min())
        observed_high = float(frame["high"].max())
        if not observed_low * 0.8 <= full_day_vwap <= observed_high * 1.2:
            raise VolumeUnitMismatchError(
                "VWAP falls outside the traded range; verify whether volume is in shares or lots"
            )
        frame["_effective_volume"] = effective_volume
        frame["_vwap"] = cumulative_vwap
        morning = frame.loc[frame["timestamp"].dt.time < pd.Timestamp("12:00").time()]
        afternoon = frame.loc[frame["timestamp"].dt.time >= pd.Timestamp("13:00").time()]
        breakout_vwap: float | None = None
        if breakout_price is not None:
            candidates = frame.index[frame["close"] >= breakout_price].tolist()
            if candidates:
                anchored = frame.loc[candidates[0] :]
                breakout_vwap = float(
                    anchored["amount"].sum() / anchored["_effective_volume"].sum()
                )
        vwap = {
            "full_day_vwap": full_day_vwap,
            "morning_vwap": self._period_vwap(morning),
            "afternoon_vwap": self._period_vwap(afternoon),
            "anchored_vwap_from_open": float(cumulative_vwap.iloc[-1]),
            "anchored_vwap_from_breakout": breakout_vwap,
        }
        opening_ranges = {
            f"opening_range_{minutes}m": self._opening_range(frame, minutes)
            for minutes in (5, 15, 30, 60)
        }
        relative_volume = self._relative_volume(frame, historical_minutes)
        volume_profile = self._volume_profile(frame)
        tpo_profile = self._tpo_profile(frame, volume_profile["price_bin_size"])
        closing_strength = self._closing_strength(frame, full_day_vwap)
        score = self._score(closing_strength, opening_ranges, volume_profile)
        return MinuteConfirmationResult(
            status="available",
            minute_score=score,
            vwap=vwap,
            opening_ranges=opening_ranges,
            relative_volume=relative_volume,
            volume_profile=volume_profile,
            tpo_profile=tpo_profile,
            closing_strength=closing_strength,
            unavailable_microstructure=dict(UNAVAILABLE_MICROSTRUCTURE),
            data_confidence="normal",
        )

    @staticmethod
    def unavailable() -> MinuteConfirmationResult:
        return MinuteConfirmationResult(
            status="unavailable",
            minute_score=None,
            vwap={},
            opening_ranges={},
            relative_volume={
                "minute_rvol": None,
                "opening_30m_rvol": None,
                "afternoon_rvol": None,
                "closing_30m_rvol": None,
            },
            volume_profile={
                "is_approximate": True,
                "notice": "Volume Profile基于1分钟K线估算，不等同于逐笔成交分布。",
            },
            tpo_profile={
                "is_approximate": True,
                "notice": "TPO基于1分钟K线近似，无法还原分钟内部价格路径。",
            },
            closing_strength={},
            unavailable_microstructure=dict(UNAVAILABLE_MICROSTRUCTURE),
            data_confidence="reduced",
        )

    @staticmethod
    def _complete_session(frame: pd.DataFrame) -> bool:
        if len(frame) < 240:
            return False
        if frame["timestamp"].dt.normalize().nunique() != 1:
            return False
        times = frame["timestamp"].dt.time
        morning = (
            (times >= pd.Timestamp("09:30").time()) & (times < pd.Timestamp("11:30").time())
        ).sum()
        afternoon = (
            (times >= pd.Timestamp("13:00").time()) & (times < pd.Timestamp("15:00").time())
        ).sum()
        return bool(morning >= 120 and afternoon >= 120)

    @staticmethod
    def _period_vwap(frame: pd.DataFrame) -> float | None:
        volume = frame["_effective_volume"].sum()
        return float(frame["amount"].sum() / volume) if volume > 0 else None

    @staticmethod
    def _opening_range(frame: pd.DataFrame, minutes: int) -> dict[str, Any]:
        section = frame.iloc[:minutes]
        high = float(section["high"].max())
        low = float(section["low"].min())
        later = frame.iloc[minutes:]
        breakout = bool((later["close"] > high).any())
        breakdown = bool((later["close"] < low).any())
        final_close = float(frame["close"].iloc[-1])
        return {
            "high": high,
            "low": low,
            "breakout": breakout,
            "breakout_hold": breakout and final_close > high,
            "failed_breakout": breakout and final_close <= high,
            "breakdown_reclaim": breakdown and final_close >= low,
        }

    def _relative_volume(
        self, frame: pd.DataFrame, historical: pd.DataFrame | None
    ) -> dict[str, float | None]:
        if historical is None or historical.empty:
            return {
                "minute_rvol": None,
                "opening_30m_rvol": None,
                "afternoon_rvol": None,
                "closing_30m_rvol": None,
            }
        history = historical.copy()
        history["timestamp"] = pd.to_datetime(history["timestamp"])
        history["minute_key"] = history["timestamp"].dt.strftime("%H:%M")
        history["trade_date"] = history["timestamp"].dt.date
        complete_dates = [
            trade_date
            for trade_date, session in history.groupby("trade_date", sort=True)
            if self._complete_session(session)
        ]
        if len(complete_dates) < 20:
            return {
                "minute_rvol": None,
                "opening_30m_rvol": None,
                "afternoon_rvol": None,
                "closing_30m_rvol": None,
            }
        last_dates = complete_dates[-20:]
        history = history.loc[history["trade_date"].isin(last_dates)]
        baseline = history.groupby("minute_key")["volume"].mean()
        current = frame.copy()
        current["minute_key"] = current["timestamp"].dt.strftime("%H:%M")
        current["_rvol"] = current["volume"] / current["minute_key"].map(baseline)
        return {
            "minute_rvol": self._safe_mean(current["_rvol"]),
            "opening_30m_rvol": self._safe_mean(current.iloc[:30]["_rvol"]),
            "afternoon_rvol": self._safe_mean(
                current.loc[current["timestamp"].dt.time >= pd.Timestamp("13:00").time(), "_rvol"]
            ),
            "closing_30m_rvol": self._safe_mean(current.iloc[-30:]["_rvol"]),
        }

    @staticmethod
    def _volume_profile(frame: pd.DataFrame) -> dict[str, Any]:
        price_range = float(frame["high"].max() - frame["low"].min())
        bin_size = max(price_range / 40.0, float(frame["close"].median()) * 0.001)
        typical = (frame["high"] + frame["low"] + frame["close"]) / 3
        bins = (typical / bin_size).round() * bin_size
        distribution = frame.groupby(bins)["_effective_volume"].sum().sort_index()
        poc = float(distribution.idxmax())
        selected = distribution.sort_values(ascending=False)
        selected = selected.loc[selected.cumsum() <= distribution.sum() * 0.7]
        if selected.empty:
            selected = distribution.nlargest(1)
        value_prices = selected.index.astype(float)
        threshold_high = float(distribution.quantile(0.8))
        threshold_low = float(distribution.quantile(0.2))
        return {
            "POC": poc,
            "VAH": float(cast(Any, max(value_prices))),
            "VAL": float(cast(Any, min(value_prices))),
            "HVN": [
                float(cast(Any, item))
                for item in distribution[distribution >= threshold_high].index
            ],
            "LVN": [
                float(cast(Any, item)) for item in distribution[distribution <= threshold_low].index
            ],
            "profile_distribution": {
                f"{float(cast(Any, price)):.4f}": float(volume)
                for price, volume in distribution.items()
            },
            "profile_method": "typical_price_bin",
            "price_bin_size": bin_size,
            "source_granularity": "1m",
            "is_approximate": True,
            "notice": "Volume Profile基于1分钟K线估算，不等同于逐笔成交分布。",
        }

    @staticmethod
    def _tpo_profile(frame: pd.DataFrame, bin_size: float) -> dict[str, Any]:
        counts: dict[float, int] = {}
        for row in frame.itertuples():
            low_bin = int(np.floor(float(cast(Any, row.low)) / bin_size))
            high_bin = int(np.ceil(float(cast(Any, row.high)) / bin_size))
            for value in range(low_bin, high_bin + 1):
                price = round(value * bin_size, 8)
                counts[price] = counts.get(price, 0) + 1
        distribution = pd.Series(counts, dtype=float).sort_index()
        poc = float(distribution.idxmax())
        selected = distribution.sort_values(ascending=False)
        selected = selected.loc[selected.cumsum() <= distribution.sum() * 0.7]
        if selected.empty:
            selected = distribution.nlargest(1)
        midpoint = len(frame) // 2
        early_value = float(frame.iloc[:midpoint]["close"].mean())
        late_value = float(frame.iloc[midpoint:]["close"].mean())
        low_acceptance = distribution[distribution <= distribution.quantile(0.2)].index
        return {
            "TPO_POC": poc,
            "TPO_VAH": float(cast(Any, max(selected.index))),
            "TPO_VAL": float(cast(Any, min(selected.index))),
            "time_acceptance": {
                f"{float(cast(Any, price)):.4f}": int(count)
                for price, count in distribution.items()
            },
            "value_area_migration": late_value - early_value,
            "fast_traversal_zone": [float(cast(Any, item)) for item in low_acceptance],
            "source_granularity": "1m",
            "is_approximate": True,
            "notice": "TPO基于1分钟K线近似，无法还原分钟内部价格路径。",
        }

    @staticmethod
    def _closing_strength(
        frame: pd.DataFrame, full_day_vwap: float
    ) -> dict[str, float | bool | None]:
        last = frame.iloc[-30:]
        first_close = float(last["close"].iloc[0])
        final_close = float(last["close"].iloc[-1])
        full_average = float(frame["volume"].mean())
        afternoon = frame.loc[frame["timestamp"].dt.time >= pd.Timestamp("13:00").time()]
        afternoon_vwap = float(afternoon["amount"].sum() / afternoon["_effective_volume"].sum())
        return {
            "last_30m_return": final_close / first_close - 1.0,
            "last_30m_volume_ratio": float(last["volume"].mean() / full_average),
            "close_above_vwap": final_close > full_day_vwap,
            "close_near_intraday_high": final_close
            >= float(frame["high"].max()) - 0.1 * float(frame["high"].max() - frame["low"].min()),
            "afternoon_vwap_reclaim": bool(
                (afternoon["close"] < afternoon_vwap).any() and final_close > afternoon_vwap
            ),
        }

    @staticmethod
    def _score(
        closing: dict[str, float | bool | None],
        opening: dict[str, dict[str, Any]],
        volume_profile: dict[str, Any],
    ) -> float:
        score = 0.0
        score += 2.0 if closing["close_above_vwap"] else 0.0
        score += 2.0 if closing["close_near_intraday_high"] else 0.0
        score += 1.5 if closing["afternoon_vwap_reclaim"] else 0.0
        score += 1.5 if float(closing["last_30m_return"] or 0.0) > 0 else 0.0
        score += 1.5 if opening["opening_range_30m"]["breakout_hold"] else 0.0
        score -= 2.0 if opening["opening_range_30m"]["failed_breakout"] else 0.0
        score += 1.5 if float(volume_profile["POC"]) <= float(volume_profile["VAH"]) else 0.0
        return round(min(max(score, 0.0), 10.0), 4)

    @staticmethod
    def _safe_mean(values: pd.Series) -> float | None:
        result = float(values.replace([np.inf, -np.inf], np.nan).mean())
        return result if np.isfinite(result) else None
