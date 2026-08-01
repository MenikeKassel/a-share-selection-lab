from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CandidateScore:
    base_daily_score: float
    pa_score: float
    wyckoff_score: float
    minute_score: float | None
    risk_penalty: float
    total_score: float | None
    eligible: bool
    hard_gate_reasons: list[str]
    data_confidence: str


class CandidateScorer:
    def score(
        self,
        *,
        base_daily_score: float,
        pa_score: float,
        wyckoff_score: float,
        minute_score: float | None,
        hard_gate_reasons: list[str],
        risk_penalty: float = 0.0,
    ) -> CandidateScore:
        risk_penalty = max(float(risk_penalty), 0.0)
        if hard_gate_reasons:
            return CandidateScore(
                base_daily_score=base_daily_score,
                pa_score=pa_score,
                wyckoff_score=wyckoff_score,
                minute_score=minute_score,
                risk_penalty=risk_penalty,
                total_score=None,
                eligible=False,
                hard_gate_reasons=hard_gate_reasons,
                data_confidence="blocked",
            )
        available = 70.0 + 10.0 + 10.0
        total = base_daily_score + pa_score + wyckoff_score - risk_penalty
        confidence = "reduced"
        if minute_score is not None:
            available += 10.0
            total += minute_score
            confidence = "normal"
        normalized = round(max(total, 0.0) / available * 100.0, 6)
        return CandidateScore(
            base_daily_score=base_daily_score,
            pa_score=pa_score,
            wyckoff_score=wyckoff_score,
            minute_score=minute_score,
            risk_penalty=risk_penalty,
            total_score=normalized,
            eligible=True,
            hard_gate_reasons=[],
            data_confidence=confidence,
        )
