from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.serializers import engine_run_dict
from app.db.repositories import EngineRunRepository
from app.db.session import get_db
from app.engines.registry import EngineRegistry

router = APIRouter(tags=["research-engines"])


@router.get("/engines")
@router.get("/engines/status")
def engine_statuses(session: Session = Depends(get_db)) -> list[dict[str, object]]:
    last_runs = {
        key: engine_run_dict(value)
        for key, value in EngineRunRepository(session).latest_by_engine().items()
    }
    return [status.as_dict() for status in EngineRegistry().statuses(last_runs=last_runs)]
