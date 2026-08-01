from __future__ import annotations

import json

import numpy as np
import pandas as pd

WYCKOFF_COLUMNS = [
    "symbol",
    "signal_type",
    "factor_code",
    "signal_date",
    "price_zone",
    "volume_condition",
    "close_location",
    "confirmation_status",
    "confidence",
    "supporting_evidence",
    "contradicting_evidence",
    "alternative_explanation",
]


class WyckoffCandidateDetector:
    """Heuristic candidates with explicit evidence and alternative explanations."""

    def detect(self, daily: pd.DataFrame) -> pd.DataFrame:
        required = {"date", "symbol", "high", "low", "close", "volume"}
        if missing := required.difference(daily.columns):
            raise ValueError(f"Wyckoff input is missing columns: {sorted(missing)}")
        frame = daily.copy()
        frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
        rows: list[dict[str, object]] = []
        for symbol, group in frame.sort_values("date").groupby("symbol", sort=False):
            rows.extend(self._detect_symbol(str(symbol), group.reset_index(drop=True)))
        return pd.DataFrame(rows, columns=WYCKOFF_COLUMNS)

    def _detect_symbol(self, symbol: str, group: pd.DataFrame) -> list[dict[str, object]]:
        if len(group) < 20:
            return []
        high = group["high"].astype(float)
        low = group["low"].astype(float)
        close = group["close"].astype(float)
        volume = group["volume"].astype(float)
        prior_high = high.shift(1).rolling(20, min_periods=10).max()
        prior_low = low.shift(1).rolling(20, min_periods=10).min()
        volume_mean = volume.shift(1).rolling(20, min_periods=10).mean()
        price_range = (high - low).replace(0, np.nan)
        close_location = (close - low) / price_range
        return_5d = close.pct_change(5, fill_method=None)
        results: list[dict[str, object]] = []

        for index in range(len(group)):
            candidates: list[tuple[str, bool, list[str], str]] = [
                (
                    "selling_climax_candidate",
                    bool(
                        low.iloc[index] <= prior_low.iloc[index]
                        and volume.iloc[index] > volume_mean.iloc[index] * 1.8
                        and close_location.iloc[index] > 0.55
                    ),
                    ["触及阶段低位", "成交量显著放大", "收盘脱离日内低点"],
                    "恐慌性抛售后的技术反弹也可能形成相同结构",
                ),
                (
                    "automatic_rally_candidate",
                    bool(
                        return_5d.iloc[index] > 0.05
                        and close.iloc[index] > prior_low.iloc[index] * 1.06
                        and close_location.iloc[index] > 0.6
                    ),
                    ["阶段低点后出现快速反弹", "五日收益转强", "收盘位置偏强"],
                    "超跌反弹或事件修复也可能形成相同形态",
                ),
                (
                    "secondary_test_candidate",
                    bool(
                        low.iloc[index] <= prior_low.iloc[index] * 1.02
                        and low.iloc[index] >= prior_low.iloc[index] * 0.98
                        and close.iloc[index] > prior_low.iloc[index]
                        and volume.iloc[index] < volume_mean.iloc[index]
                    ),
                    ["回测阶段支撑区", "收盘守住前低", "成交量低于近期均值"],
                    "低波动横盘中的普通支撑测试仍需区分",
                ),
                (
                    "spring_candidate",
                    bool(
                        low.iloc[index] < prior_low.iloc[index]
                        and close.iloc[index] > prior_low.iloc[index]
                        and close_location.iloc[index] > 0.6
                    ),
                    ["短暂跌破前低", "收盘重新回到支撑区", "收盘位置偏强"],
                    "普通假跌破或事件波动，仍需后续确认",
                ),
                (
                    "upthrust_candidate",
                    bool(
                        high.iloc[index] > prior_high.iloc[index]
                        and close.iloc[index] < prior_high.iloc[index]
                        and close_location.iloc[index] < 0.4
                    ),
                    ["盘中突破前高", "收盘跌回阻力区", "收盘位置偏弱"],
                    "短线获利回吐也可能造成上影线",
                ),
                (
                    "sign_of_strength_candidate",
                    bool(
                        close.iloc[index] > prior_high.iloc[index]
                        and volume.iloc[index] > volume_mean.iloc[index] * 1.2
                        and close_location.iloc[index] > 0.65
                    ),
                    ["收盘突破阶段高点", "成交量高于近期均值", "收盘靠近日内高位"],
                    "消息驱动的单日跳升可能缺乏持续性",
                ),
                (
                    "sign_of_weakness_candidate",
                    bool(
                        close.iloc[index] < prior_low.iloc[index]
                        and volume.iloc[index] > volume_mean.iloc[index] * 1.2
                        and close_location.iloc[index] < 0.35
                    ),
                    ["收盘跌破阶段低点", "成交量高于近期均值", "收盘靠近日内低位"],
                    "除权或一次性事件可能扭曲价格结构",
                ),
                (
                    "last_point_of_support_candidate",
                    bool(
                        low.iloc[index] > prior_low.iloc[index]
                        and close.iloc[index] < prior_high.iloc[index] * 0.98
                        and close_location.iloc[index] > 0.5
                        and volume.iloc[index] < volume_mean.iloc[index] * 0.9
                    ),
                    ["回调未破阶段支撑", "回调成交量收缩", "收盘位于区间中上部"],
                    "上升趋势中的普通缩量回调也可能出现该候选",
                ),
                (
                    "last_point_of_supply_candidate",
                    bool(
                        high.iloc[index] < prior_high.iloc[index]
                        and close_location.iloc[index] < 0.4
                        and close.iloc[index] < close.rolling(10, min_periods=5).mean().iloc[index]
                    ),
                    ["反弹未越过阶段阻力", "收盘位置偏弱", "价格低于短期均值"],
                    "弱势震荡中的普通反弹失败也可能形成该候选",
                ),
            ]
            for signal_type, condition, evidence, alternative in candidates:
                if condition:
                    results.append(
                        self._record(
                            symbol,
                            signal_type,
                            group.iloc[index],
                            float(prior_low.iloc[index]),
                            float(prior_high.iloc[index]),
                            float(close_location.iloc[index]),
                            float(volume.iloc[index] / volume_mean.iloc[index]),
                            evidence,
                            alternative,
                        )
                    )
        return results

    @staticmethod
    def _record(
        symbol: str,
        signal_type: str,
        row: pd.Series,
        support: float,
        resistance: float,
        close_location: float,
        volume_ratio: float,
        evidence: list[str],
        alternative: str,
    ) -> dict[str, object]:
        confidence = min(0.95, 0.45 + 0.08 * len(evidence) + 0.05 * min(volume_ratio, 3))
        return {
            "symbol": symbol,
            "signal_type": signal_type,
            "factor_code": {
                "sign_of_strength_candidate": "sos_candidate",
                "last_point_of_support_candidate": "lps_candidate",
            }.get(signal_type, signal_type),
            "signal_date": pd.Timestamp(row["date"]).date().isoformat(),
            "price_zone": {"support": support, "resistance": resistance},
            "volume_condition": {"ratio_to_20d": volume_ratio},
            "close_location": close_location,
            "confirmation_status": "candidate_unconfirmed",
            "confidence": round(confidence, 4),
            "supporting_evidence": json.dumps(evidence, ensure_ascii=False),
            "contradicting_evidence": json.dumps(["尚未观察到后续交易日确认"], ensure_ascii=False),
            "alternative_explanation": alternative,
        }
