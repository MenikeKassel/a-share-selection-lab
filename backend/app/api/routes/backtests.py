from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.schemas import (
    FormalBacktestRunRequest,
    RQAlphaValidationRequest,
    VectorBTResearchRequest,
)
from app.api.serializers import backtest_run_dict, engine_run_dict
from app.core.config import Settings, get_settings
from app.db.repositories import BacktestRunRepository, EngineRunRepository
from app.db.session import get_db
from app.services.research_tasks import ResearchTaskService

router = APIRouter(tags=["backtests"])


@router.post("/research-backtests/vectorbt", status_code=status.HTTP_202_ACCEPTED)
def run_vectorbt(
    payload: VectorBTResearchRequest,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    record = ResearchTaskService(session, settings).run_vectorbt(payload)
    return engine_run_dict(record)


@router.get("/research-backtests/{run_id}")
def get_research_backtest(run_id: int, session: Session = Depends(get_db)) -> dict[str, object]:
    record = EngineRunRepository(session).get(run_id)
    if record is None or record.engine_type != "vectorbt":
        raise HTTPException(status_code=404, detail="VectorBT run not found")
    return engine_run_dict(record)


@router.post("/validation-backtests/rqalpha", status_code=status.HTTP_202_ACCEPTED)
def run_rqalpha(
    payload: RQAlphaValidationRequest,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    record = ResearchTaskService(session, settings).run_rqalpha(payload)
    return engine_run_dict(record)


@router.get("/validation-backtests/{run_id}")
def get_validation_backtest(run_id: int, session: Session = Depends(get_db)) -> dict[str, object]:
    record = EngineRunRepository(session).get(run_id)
    if record is None or record.engine_type != "rqalpha":
        raise HTTPException(status_code=404, detail="RQAlpha run not found")
    return engine_run_dict(record)


@router.post("/backtests", status_code=status.HTTP_201_CREATED)
def run_formal_backtest(
    payload: FormalBacktestRunRequest,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    record = ResearchTaskService(session, settings).run_formal_backtest(payload)
    return backtest_run_dict(record)


@router.get("/backtests")
def list_formal_backtests(
    session: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return [backtest_run_dict(item) for item in BacktestRunRepository(session).list()]


@router.get("/backtests/{run_id}")
def get_formal_backtest(run_id: int, session: Session = Depends(get_db)) -> dict[str, object]:
    record = BacktestRunRepository(session).get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="formal backtest not found")
    return backtest_run_dict(record)
