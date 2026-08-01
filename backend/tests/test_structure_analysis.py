import pandas as pd
import pytest
from app.research.minute.confirmation import MinuteConfirmationAnalyzer
from app.research.pa import PriceActionAnalyzer, SwingPointConfig
from app.research.scoring import CandidateScorer
from app.research.wyckoff import WyckoffCandidateDetector


def test_missing_minute_confirmation_is_reweighted_not_zeroed() -> None:
    score = CandidateScorer().score(
        base_daily_score=56.0,
        pa_score=8.0,
        wyckoff_score=7.0,
        minute_score=None,
        hard_gate_reasons=[],
    )

    assert score.minute_score is None
    assert score.total_score == 78.888889
    assert score.data_confidence == "reduced"


def test_hard_gate_cannot_be_offset_by_high_scores() -> None:
    score = CandidateScorer().score(
        base_daily_score=70.0,
        pa_score=10.0,
        wyckoff_score=10.0,
        minute_score=10.0,
        hard_gate_reasons=["confirmed_failed_breakout"],
    )

    assert score.eligible is False
    assert score.total_score is None


def test_risk_penalty_is_subtracted_outside_the_base_factor_score() -> None:
    score = CandidateScorer().score(
        base_daily_score=56.0,
        pa_score=8.0,
        wyckoff_score=7.0,
        minute_score=None,
        risk_penalty=9.0,
        hard_gate_reasons=[],
    )

    assert score.risk_penalty == 9.0
    assert score.total_score == 68.888889


def test_minute_profile_is_explicitly_approximate_and_no_fake_order_flow() -> None:
    rows = []
    session = list(pd.date_range("2026-01-05 09:30", periods=120, freq="min"))
    session += list(pd.date_range("2026-01-05 13:00", periods=120, freq="min"))
    for index, timestamp in enumerate(session):
        price = 10 + index * 0.001
        rows.append(
            {
                "timestamp": timestamp,
                "open": price,
                "high": price + 0.01,
                "low": price - 0.01,
                "close": price,
                "volume": 10_000,
                "amount": price * 10_000,
            }
        )

    result = MinuteConfirmationAnalyzer().analyze(pd.DataFrame(rows))

    assert result.status == "available"
    assert result.volume_profile["is_approximate"] is True
    assert "1分钟K线估算" in result.volume_profile["notice"]
    assert "无法还原分钟内部价格路径" in result.tpo_profile["notice"]
    assert result.unavailable_microstructure["cvd"] == "unavailable"
    assert result.unavailable_microstructure["footprint"] == "unavailable"


def test_minute_confirmation_cannot_combine_partial_sessions_across_dates() -> None:
    rows = []
    for trade_date in ("2026-01-05", "2026-01-06"):
        session = list(pd.date_range(f"{trade_date} 09:30", periods=60, freq="min"))
        session += list(pd.date_range(f"{trade_date} 13:00", periods=60, freq="min"))
        for timestamp in session:
            rows.append(
                {
                    "timestamp": timestamp,
                    "open": 10.0,
                    "high": 10.01,
                    "low": 9.99,
                    "close": 10.0,
                    "volume": 10_000,
                    "amount": 100_000,
                }
            )

    result = MinuteConfirmationAnalyzer().analyze_for_date(
        pd.DataFrame(rows), pd.Timestamp("2026-01-06").date()
    )

    assert result.status == "unavailable"
    assert result.minute_score is None


def test_minute_rvol_uses_twenty_complete_prior_same_minute_sessions() -> None:
    rows = []
    dates = pd.bdate_range("2025-12-08", periods=21)
    for trade_date in dates:
        session = list(pd.date_range(f"{trade_date.date()} 09:30", periods=120, freq="min"))
        session += list(pd.date_range(f"{trade_date.date()} 13:00", periods=120, freq="min"))
        volume = 20_000 if trade_date == dates[-1] else 10_000
        for timestamp in session:
            rows.append(
                {
                    "timestamp": timestamp,
                    "open": 10.0,
                    "high": 10.01,
                    "low": 9.99,
                    "close": 10.0,
                    "volume": volume,
                    "amount": 10.0 * volume,
                }
            )

    result = MinuteConfirmationAnalyzer().analyze_for_date(pd.DataFrame(rows), dates[-1].date())

    assert result.status == "available"
    assert result.relative_volume["minute_rvol"] == pytest.approx(2.0)


def test_pa_swing_is_emitted_only_after_the_right_window_confirms_it() -> None:
    lows = [10.0, 9.0, 8.0, 9.0, 10.0, 9.0, 8.5, 9.5, 10.5]
    rows = [
        {
            "date": trade_date,
            "symbol": "A",
            "open": low + 0.5,
            "high": low + 1.0,
            "low": low,
            "close": low + 0.6,
            "volume": 100_000,
        }
        for trade_date, low in zip(
            pd.bdate_range("2026-01-02", periods=len(lows)), lows, strict=True
        )
    ]
    analyzer = PriceActionAnalyzer(
        SwingPointConfig(
            left_window=1,
            right_window=1,
            atr_filter=0,
            min_price_change=0,
            min_trading_day_interval=1,
        )
    )
    full = analyzer.analyze(pd.DataFrame(rows))
    prefix = analyzer.analyze(pd.DataFrame(rows[:7]))

    cutoff = prefix["date"].max()
    full_at_cutoff = full.loc[full["date"] == cutoff].iloc[0]
    prefix_at_cutoff = prefix.iloc[-1]
    assert bool(full_at_cutoff["swing_low"]) is False
    assert bool(prefix_at_cutoff["swing_low"]) is False
    assert full_at_cutoff["higher_low"] == prefix_at_cutoff["higher_low"]


def test_wyckoff_output_uses_candidate_language_and_evidence() -> None:
    rows = []
    for index, trade_date in enumerate(pd.bdate_range("2026-01-02", periods=65)):
        close = 10 + index * 0.01
        rows.append(
            {
                "date": trade_date,
                "symbol": "A",
                "open": close - 0.02,
                "high": close + 0.05,
                "low": close - 0.05,
                "close": close,
                "volume": 1_000_000,
            }
        )

    output = WyckoffCandidateDetector().detect(pd.DataFrame(rows))

    assert {
        "signal_date",
        "price_zone",
        "volume_condition",
        "close_location",
        "confirmation_status",
        "confidence",
        "supporting_evidence",
        "contradicting_evidence",
        "alternative_explanation",
    }.issubset(output.columns)
    serialized = output.to_json(force_ascii=False)
    for forbidden in ("主力确定吸筹", "主力确定出货", "庄家控盘", "机构订单已出现"):
        assert forbidden not in serialized
