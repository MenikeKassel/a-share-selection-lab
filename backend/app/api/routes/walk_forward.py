from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.schemas import WalkForwardRunRequest
from app.api.serializers import walk_forward_dict
from app.core.config import Settings, get_settings
from app.db.repositories import WalkForwardRepository
from app.db.session import get_db
from app.services.walk_forward import WalkForwardTaskService

router = APIRouter(tags=["walk-forward"])


@router.post(
    "/walk-forward-experiments",
    status_code=status.HTTP_202_ACCEPTED,
)
@router.post("/walk-forward", status_code=status.HTTP_202_ACCEPTED, include_in_schema=False)
def run_walk_forward(
    payload: WalkForwardRunRequest,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    record = WalkForwardTaskService(session, settings).run(payload)
    return walk_forward_dict(record)


@router.get("/walk-forward-experiments")
@router.get("/walk-forward", include_in_schema=False)
def list_walk_forward(
    session: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return [walk_forward_dict(item) for item in WalkForwardRepository(session).list()]


@router.get("/walk-forward-experiments/{experiment_id}")
@router.get("/walk-forward/{experiment_id}", include_in_schema=False)
def get_walk_forward(
    experiment_id: int,
    session: Session = Depends(get_db),
) -> dict[str, object]:
    record = WalkForwardRepository(session).get(experiment_id)
    if record is None:
        raise HTTPException(status_code=404, detail="walk-forward experiment not found")
    return walk_forward_dict(record)
