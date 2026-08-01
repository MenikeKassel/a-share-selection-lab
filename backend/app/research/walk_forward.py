from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date
from typing import Literal

LifecycleStatus = Literal[
    "experimental",
    "validated",
    "production_candidate",
    "production",
    "retired",
]


@dataclass(frozen=True, slots=True)
class WalkForwardSplit:
    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    test_start: date
    test_end: date

    def __post_init__(self) -> None:
        if not (
            self.train_start
            <= self.train_end
            < self.validation_start
            <= self.validation_end
            < self.test_start
            <= self.test_end
        ):
            raise ValueError("walk-forward ranges must be strictly ordered")


@dataclass(frozen=True, slots=True)
class RobustnessEvidence:
    consistent_across_periods: bool
    stable_nearby_parameters: bool
    survives_costs: bool
    not_driven_by_extremes: bool
    cross_industry: bool
    acceptable_drawdown: bool
    stable_ic_direction: bool
    out_of_sample_healthy: bool


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    status: LifecycleStatus
    production_enabled: bool
    passed_checks: list[str]
    failed_checks: list[str]
    reason: str


class WalkForwardPolicy:
    def evaluate(
        self,
        evidence: RobustnessEvidence,
        *,
        source_engine: str,
        manual_production_approval: bool,
    ) -> PromotionDecision:
        passed = [item.name for item in fields(evidence) if bool(getattr(evidence, item.name))]
        failed = [item.name for item in fields(evidence) if not bool(getattr(evidence, item.name))]
        if failed:
            status: LifecycleStatus = "experimental"
            reason = "稳健性门禁未全部通过。"
        elif source_engine in {"vectorbt", "qlib"} and not manual_production_approval:
            status = "production_candidate"
            reason = "研究引擎结果已通过门禁，但必须人工审批且由正式A股引擎复核。"
        elif manual_production_approval:
            status = "production"
            reason = "已记录人工生产审批。"
        else:
            status = "validated"
            reason = "已通过稳健性验证，尚未批准进入生产。"
        return PromotionDecision(
            status=status,
            production_enabled=status == "production",
            passed_checks=passed,
            failed_checks=failed,
            reason=reason,
        )


def generate_annual_walk_forward_splits(
    *,
    first_train_year: int,
    final_test_year: int,
    train_years: int = 3,
    validation_years: int = 1,
    test_years: int = 1,
) -> list[WalkForwardSplit]:
    if min(train_years, validation_years, test_years) < 1:
        raise ValueError("walk-forward window lengths must be positive")
    splits: list[WalkForwardSplit] = []
    train_start_year = first_train_year
    while True:
        train_end_year = train_start_year + train_years - 1
        validation_start_year = train_end_year + 1
        validation_end_year = validation_start_year + validation_years - 1
        test_start_year = validation_end_year + 1
        test_end_year = test_start_year + test_years - 1
        if test_end_year > final_test_year:
            break
        splits.append(
            WalkForwardSplit(
                train_start=date(train_start_year, 1, 1),
                train_end=date(train_end_year, 12, 31),
                validation_start=date(validation_start_year, 1, 1),
                validation_end=date(validation_end_year, 12, 31),
                test_start=date(test_start_year, 1, 1),
                test_end=date(test_end_year, 12, 31),
            )
        )
        train_start_year += 1
    return splits
