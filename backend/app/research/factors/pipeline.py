from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

Normalization = Literal["percentile", "zscore", "robust_zscore"]


@dataclass(frozen=True, slots=True)
class FactorDefinition:
    code: str
    group: str
    weight: float
    direction: Literal[-1, 1] = 1
    risk: bool = False


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    normalization: Normalization = "robust_zscore"
    winsorize_limits: tuple[float, float] = (0.01, 0.99)
    industry_neutral: bool = False
    market_cap_neutral: bool = False
    calculation_version: str = "factor_pipeline_v1"


@dataclass(frozen=True, slots=True)
class FactorPipelineOutput:
    scores: pd.DataFrame
    details: pd.DataFrame


class FactorPipeline:
    """Cross-sectional processing with explicit missing-value reweighting."""

    def __init__(
        self,
        definitions: list[FactorDefinition],
        config: PipelineConfig | None = None,
    ) -> None:
        if not definitions:
            raise ValueError("at least one factor definition is required")
        self.definitions = definitions
        self.config = config or PipelineConfig()

    def transform(self, raw: pd.DataFrame) -> FactorPipelineOutput:
        required = {"date", "symbol", *(item.code for item in self.definitions)}
        missing = required.difference(raw.columns)
        if missing:
            raise ValueError(f"factor input is missing columns: {sorted(missing)}")

        frame = raw.copy()
        frame["date"] = pd.to_datetime(frame["date"])
        detail_frames: list[pd.DataFrame] = []
        for definition in self.definitions:
            detail_frames.append(self._transform_factor(frame, definition))
        details = pd.concat(detail_frames, ignore_index=True)

        valid = details.loc[~details["is_missing"]].copy()
        valid["weighted_value"] = valid["normalized_value"] * valid["weight"]
        valid["available_weight_component"] = valid["weight"].abs()
        aggregates = (
            valid.groupby(["date", "symbol"], as_index=False)
            .agg(
                weighted_sum=("weighted_value", "sum"),
                available_weight=("available_weight_component", "sum"),
                risk_contribution=("risk_contribution", "sum"),
            )
            .reset_index(drop=True)
        )
        base = frame[["date", "symbol"]].drop_duplicates()
        scores = base.merge(aggregates, on=["date", "symbol"], how="left")
        scores["available_weight"] = scores["available_weight"].fillna(0.0)
        scores["risk_contribution"] = scores["risk_contribution"].fillna(0.0)
        scores["composite_score"] = np.where(
            scores["available_weight"] > 0,
            scores["weighted_sum"] / scores["available_weight"] * 100.0,
            np.nan,
        )
        scores["composite_score"] = (
            scores["composite_score"] - scores["risk_contribution"].clip(lower=0) * 100.0
        )
        scores["data_quality"] = np.where(
            scores["available_weight"] >= sum(abs(item.weight) for item in self.definitions) * 0.8,
            "normal",
            "reduced",
        )
        details["factor_contribution"] = details["normalized_value"] * details["weight"] * 100.0
        details["calculation_version"] = self.config.calculation_version
        return FactorPipelineOutput(scores=scores, details=details)

    def _transform_factor(self, frame: pd.DataFrame, definition: FactorDefinition) -> pd.DataFrame:
        columns = ["date", "symbol", definition.code]
        for optional in ("industry", "market_cap"):
            if optional in frame.columns:
                columns.append(optional)
        result = frame[columns].rename(columns={definition.code: "raw_value"}).copy()
        result["raw_value"] = pd.to_numeric(result["raw_value"], errors="coerce")
        result["is_missing"] = result["raw_value"].isna()

        processed_parts: list[pd.DataFrame] = []
        for _, cross_section in result.groupby("date", sort=False):
            section = cross_section.copy()
            section["processed_value"] = self._process_cross_section(section)
            processed_parts.append(section)
        result = pd.concat(processed_parts, ignore_index=True)

        result["processed_value"] = result["processed_value"] * definition.direction
        result["percentile"] = result.groupby("date")["processed_value"].rank(pct=True)
        result["zscore"] = result.groupby("date")["processed_value"].transform(self._zscore)
        result["robust_zscore"] = result.groupby("date")["processed_value"].transform(
            self._robust_zscore
        )
        if self.config.normalization == "percentile":
            result["normalized_value"] = result["percentile"]
        elif self.config.normalization == "zscore":
            result["normalized_value"] = result["zscore"]
        else:
            result["normalized_value"] = result["robust_zscore"]
        result["factor_code"] = definition.code
        result["factor_group"] = definition.group
        result["weight"] = definition.weight
        result["data_quality"] = np.where(result["is_missing"], "missing", "normal")
        result["risk_contribution"] = np.where(
            definition.risk & ~result["is_missing"],
            result["normalized_value"].clip(lower=0) * abs(definition.weight),
            0.0,
        )
        return result[
            [
                "date",
                "symbol",
                "factor_code",
                "factor_group",
                "raw_value",
                "processed_value",
                "percentile",
                "zscore",
                "robust_zscore",
                "normalized_value",
                "weight",
                "is_missing",
                "data_quality",
                "risk_contribution",
            ]
        ]

    def _process_cross_section(self, section: pd.DataFrame) -> pd.Series:
        # Keep the neutralisation residual floating-point even when a source
        # factor is encoded as an integer/bool column.  Assignment of the
        # regression residual back into an integer Series otherwise emits a
        # pandas warning and will become an error in a future pandas release.
        values = pd.to_numeric(section["raw_value"], errors="coerce").astype(float)
        valid = values.dropna()
        if not valid.empty:
            low, high = self.config.winsorize_limits
            values = values.clip(valid.quantile(low), valid.quantile(high))
        if self.config.industry_neutral and "industry" in section:
            values = values - values.groupby(section["industry"]).transform("mean")
        if self.config.market_cap_neutral and "market_cap" in section:
            values = self._neutralize_market_cap(values, section["market_cap"])
        return values

    @staticmethod
    def _neutralize_market_cap(values: pd.Series, market_cap: pd.Series) -> pd.Series:
        output = values.copy()
        valid = values.notna() & market_cap.notna() & (market_cap > 0)
        if valid.sum() < 3:
            return output
        x = np.column_stack([np.ones(int(valid.sum())), np.log(market_cap.loc[valid].to_numpy())])
        y = values.loc[valid].to_numpy()
        coefficients, *_ = np.linalg.lstsq(x, y, rcond=None)
        output.loc[valid] = y - x @ coefficients
        return output

    @staticmethod
    def _zscore(values: pd.Series) -> pd.Series:
        deviation = values.std(ddof=0)
        if pd.isna(deviation) or deviation == 0:
            return pd.Series(0.0, index=values.index).where(values.notna())
        return (values - values.mean()) / deviation

    @staticmethod
    def _robust_zscore(values: pd.Series) -> pd.Series:
        median = values.median()
        mad = (values - median).abs().median()
        if pd.isna(mad) or mad == 0:
            return FactorPipeline._zscore(values)
        return (values - median) / (1.4826 * mad)
