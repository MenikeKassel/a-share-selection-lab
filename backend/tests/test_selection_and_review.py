from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from app.db.session import create_session_factory, initialize_database
from app.selection.pipeline import DailySelectionPipeline
from app.selection.review import AutomaticReviewService
from app.selection.snapshots import (
    ImmutableSnapshotError,
    SelectionSnapshotRepository,
    data_snapshot_version,
    write_candidate_artifact,
)


def test_selection_snapshot_cannot_be_overwritten(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    initialize_database(database_url)
    factory = create_session_factory(database_url)
    with factory() as session:
        repository = SelectionSnapshotRepository(session)
        first = repository.create(
            selection_date=date(2026, 1, 2),
            strategy_code="trend_quality_v1",
            strategy_version="1.0.0",
            factor_version="factor_v1",
            data_snapshot_version="sha256:abc",
            selection_status="ready",
            candidates=[{"symbol": "A", "total_score": 80.0}],
            artifact_path=None,
        )
        assert first.id is not None

        with pytest.raises(ImmutableSnapshotError):
            repository.create(
                selection_date=date(2026, 1, 2),
                strategy_code="trend_quality_v1",
                strategy_version="1.0.0",
                factor_version="factor_v1",
                data_snapshot_version="sha256:abc",
                selection_status="ready",
                candidates=[],
                artifact_path=None,
            )


def test_data_snapshot_hash_covers_minute_and_fundamental_inputs() -> None:
    daily = pd.DataFrame([{"date": "2026-01-02", "symbol": "A", "close": 10.0}])
    minute = pd.DataFrame([{"timestamp": "2026-01-02 09:30", "close": 10.0, "volume": 100}])
    changed_minute = minute.assign(volume=200)

    first = data_snapshot_version({"daily": daily, "minute/A": minute})
    second = data_snapshot_version({"daily": daily, "minute/A": changed_minute})

    assert first != second


def test_candidate_artifact_is_versioned_and_never_overwritten(tmp_path) -> None:
    arguments = {
        "artifact_root": tmp_path,
        "selection_date": date(2026, 1, 2),
        "strategy_code": "trend_quality_v1",
        "strategy_version": "1.0.0",
        "factor_version": "factor_v1",
        "snapshot_version": "sha256:abc",
    }
    first = write_candidate_artifact(
        [{"symbol": "A", "score": 80.0}],
        **arguments,
    )

    with pytest.raises(ImmutableSnapshotError):
        write_candidate_artifact(
            [{"symbol": "B", "score": 90.0}],
            **arguments,
        )

    assert "strategy-1.0.0" in first.as_posix()
    assert "factor-factor_v1" in first.as_posix()
    assert pd.read_parquet(first)["symbol"].tolist() == ["A"]


def test_automatic_review_reports_theoretical_and_tradable_returns() -> None:
    market = pd.DataFrame(
        [
            {
                "date": "2026-01-02",
                "symbol": "A",
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 10.0,
                "industry": "行业1",
                "suspended": False,
                "limit_up": False,
                "one_word_limit_up": False,
            },
            {
                "date": "2026-01-05",
                "symbol": "A",
                "open": 10.5,
                "high": 11.2,
                "low": 10.4,
                "close": 11.0,
                "industry": "行业1",
                "suspended": False,
                "limit_up": False,
                "one_word_limit_up": False,
            },
            {
                "date": "2026-01-06",
                "symbol": "A",
                "open": 11.0,
                "high": 12.0,
                "low": 10.8,
                "close": 11.5,
                "industry": "行业1",
                "suspended": False,
                "limit_up": False,
                "one_word_limit_up": False,
            },
        ]
    )

    review = AutomaticReviewService().calculate(
        candidates=[
            {
                "symbol": "A",
                "selection_date": "2026-01-02",
                "industry": "行业1",
                "strategies": ["trend_quality_v1"],
                "higher_low": 1.0,
                "wyckoff_candidates": [{"signal_type": "spring_candidate"}],
                "minute_features": {"closing_strength": {"close_above_vwap": True}},
            }
        ],
        market=market,
        horizons=[1],
    )

    row = review.iloc[0]
    assert row["next_open_return"] == pytest.approx(0.05)
    assert row["open_to_close_return"] == pytest.approx(11.0 / 10.5 - 1)
    assert row["tradable_return"] == pytest.approx(11.0 / 10.5 - 1)
    assert row["max_favorable_excursion"] == pytest.approx(11.2 / 10.5 - 1)
    summary = AutomaticReviewService().summarize(review)
    assert summary["strategy_results"]["trend_quality_v1"]["1D"]["win_rate"] == 1.0
    assert summary["signal_results"]["spring_candidate"]["1D"]["count"] == 1
    assert summary["signal_results"]["close_above_vwap"]["1D"]["count"] == 1


def test_daily_selection_keeps_factor_audit_and_degrades_missing_minutes(
    tmp_path,
) -> None:
    dates = pd.bdate_range(end="2026-07-30", periods=140)
    rows = []
    for day_number, trade_date in enumerate(dates):
        for symbol_number, symbol in enumerate(["A", "B", "C"]):
            close = 10.0 + day_number * (0.01 + symbol_number * 0.005)
            volume = 100_000 + symbol_number * 20_000 + day_number * 100
            final_stall = trade_date == dates[-1] and symbol == "C"
            rows.append(
                {
                    "date": trade_date,
                    "symbol": symbol,
                    "open": close - 0.001,
                    "high": close + (1.0 if final_stall else 0.002),
                    "low": close - (0.1 if final_stall else 0.002),
                    "close": close,
                    "volume": 10_000_000 if final_stall else volume,
                    "amount": (10_000_000 if final_stall else volume) * close,
                    "turnover_rate": 0.01 + symbol_number * 0.002,
                    "industry": f"industry-{symbol_number % 2}",
                    "market_cap": 1_000_000_000 * (symbol_number + 1),
                    "limit_up": False,
                    "limit_down": False,
                    "one_word_limit_up": False,
                    "one_word_limit_down": False,
                    "suspended": False,
                    "is_st": False,
                    "listing_days": 500,
                    "delisting_risk": False,
                }
            )
    database_url = f"sqlite:///{(tmp_path / 'selection.db').as_posix()}"
    initialize_database(database_url)
    factory = create_session_factory(database_url)
    with factory() as session:
        result = DailySelectionPipeline(
            artifact_root=tmp_path / "artifacts",
            snapshot_repository=SelectionSnapshotRepository(session),
        ).run(
            daily=pd.DataFrame(rows),
            trading_dates=[item.date() for item in dates],
            now=datetime(2026, 7, 30, 19, tzinfo=ZoneInfo("Asia/Shanghai")),
            expected_universe_size=3,
            minute_data=None,
        )

    assert result.status == "ready"
    assert result.candidates
    assert {item["symbol"] for item in result.rejected_candidates} == {"C"}
    assert result.rejected_candidates[0]["hard_gate_reasons"] == ["high_volume_stall"]
    assert all(item["symbol"] != "C" for pool in result.strategy_pools.values() for item in pool)
    candidate = result.candidates[0]
    assert candidate["minute_score"] is None
    assert candidate["minute_confirmation"] == "unavailable"
    assert candidate["data_confidence"] == "reduced"
    assert isinstance(candidate["risk_penalty"], float)
    assert candidate["factor_details"]
    detail = candidate["factor_details"][0]
    assert {
        "raw_value",
        "processed_value",
        "percentile",
        "zscore",
        "weight",
        "factor_contribution",
        "is_missing",
        "data_quality",
        "calculation_version",
    }.issubset(detail)
    assert len(result.snapshot_ids) == 4
