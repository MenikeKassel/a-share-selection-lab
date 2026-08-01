from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.schemas import FactorAnalysisRunRequest
from app.api.serializers import engine_run_dict
from app.core.config import Settings, get_settings
from app.db.repositories import EngineRunRepository, FactorResultRepository, decode_json
from app.db.session import get_db
from app.services.research_tasks import ResearchTaskService

router = APIRouter(prefix="/factor-analysis", tags=["factor-analysis"])


@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
def run_factor_analysis(
    payload: FactorAnalysisRunRequest,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    record = ResearchTaskService(session, settings).run_factor_analysis(payload)
    return engine_run_dict(record)


@router.get("")
def list_factor_analysis(
    session: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return [
        {
            "id": item.id,
            "run_id": item.run_id,
            "factor_code": item.factor_code,
            "analysis_engine": item.analysis_engine,
            "start_date": item.start_date,
            "end_date": item.end_date,
            "horizon": item.horizon,
            "ic": item.ic,
            "rank_ic": item.rank_ic,
            "icir": item.icir,
            "long_short_return": item.long_short_return,
            "turnover": item.turnover,
            "coverage": item.coverage,
            "result": decode_json(item.result_json),
            "created_at": item.created_at,
        }
        for item in FactorResultRepository(session).list()
    ]


@router.get("/{run_id}")
def get_factor_analysis(run_id: int, session: Session = Depends(get_db)) -> dict[str, object]:
    record = EngineRunRepository(session).get(run_id)
    if record is None or record.run_type != "factor_analysis":
        raise HTTPException(status_code=404, detail="factor-analysis run not found")
    return engine_run_dict(record)
