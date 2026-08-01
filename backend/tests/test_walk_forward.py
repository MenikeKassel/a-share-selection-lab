from datetime import date

from app.api.schemas import WalkForwardRunRequest
from app.research.walk_forward import (
    RobustnessEvidence,
    WalkForwardPolicy,
    WalkForwardSplit,
    generate_annual_walk_forward_splits,
)


def test_walk_forward_ranges_must_be_ordered() -> None:
    split = WalkForwardSplit(
        train_start=date(2021, 1, 1),
        train_end=date(2023, 12, 31),
        validation_start=date(2024, 1, 1),
        validation_end=date(2024, 12, 31),
        test_start=date(2025, 1, 1),
        test_end=date(2025, 12, 31),
    )

    assert split.test_start > split.validation_end


def test_vectorbt_and_qlib_cannot_auto_promote_to_production() -> None:
    evidence = RobustnessEvidence(
        consistent_across_periods=True,
        stable_nearby_parameters=True,
        survives_costs=True,
        not_driven_by_extremes=True,
        cross_industry=True,
        acceptable_drawdown=True,
        stable_ic_direction=True,
        out_of_sample_healthy=True,
    )

    decision = WalkForwardPolicy().evaluate(
        evidence, source_engine="vectorbt", manual_production_approval=False
    )

    assert decision.status == "production_candidate"
    assert decision.production_enabled is False


def test_annual_walk_forward_windows_roll_without_overlap() -> None:
    splits = generate_annual_walk_forward_splits(
        first_train_year=2021,
        final_test_year=2026,
        train_years=3,
        validation_years=1,
        test_years=1,
    )

    assert len(splits) == 2
    assert splits[0].train_start == date(2021, 1, 1)
    assert splits[0].test_start == date(2025, 1, 1)
    assert splits[1].train_start == date(2022, 1, 1)
    assert splits[1].test_start == date(2026, 1, 1)


def test_trend_quality_request_defaults_to_36_fixed_parameter_combinations() -> None:
    payload = WalkForwardRunRequest(experiment_code="grid-test")
    grid = payload.parameter_grid
    combinations = 1
    for values in grid.values():
        combinations *= len(values)
    assert combinations == 36
    assert grid["top_n"] == [5, 10, 20]
    assert grid["slippage_bps"] == [5.0, 10.0]
