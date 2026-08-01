from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.schemas import QlibExperimentRequest
from app.api.serializers import model_experiment_dict
from app.core.config import Settings, get_settings
from app.db.repositories import ModelExperimentRepository
from app.db.session import get_db
from app.services.research_tasks import ResearchTaskService

router = APIRouter(prefix="/ml-experiments", tags=["ml-experiments"])


@router.post("/qlib", status_code=status.HTTP_202_ACCEPTED)
def run_qlib_experiment(
    payload: QlibExperimentRequest,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    _, experiment = ResearchTaskService(session, settings).run_qlib(payload)
    return model_experiment_dict(experiment)


@router.get("")
def list_experiments(
    session: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return [model_experiment_dict(item) for item in ModelExperimentRepository(session).list()]


@router.get("/{experiment_id}")
def get_experiment(experiment_id: int, session: Session = Depends(get_db)) -> dict[str, object]:
    record = ModelExperimentRepository(session).get(experiment_id)
    if record is None:
        raise HTTPException(status_code=404, detail="model experiment not found")
    return model_experiment_dict(record)
