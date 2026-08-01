from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.models import EngineComparison
from app.db.repositories import ComparisonRepository, decode_json
from app.db.session import get_db

router = APIRouter(prefix="/engine-comparisons", tags=["engine-comparisons"])


def _serialize(item: EngineComparison) -> dict[str, object]:
    return {
        "id": item.id,
        "primary_run_id": item.primary_run_id,
        "comparison_run_id": item.comparison_run_id,
        "comparison_type": item.comparison_type,
        "metrics": decode_json(item.metrics_json),
        "differences": decode_json(item.differences_json),
        "created_at": item.created_at,
    }


@router.get("")
def list_comparisons(
    session: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return [_serialize(item) for item in ComparisonRepository(session).list()]


@router.get("/{comparison_id}")
def get_comparison(comparison_id: int, session: Session = Depends(get_db)) -> dict[str, object]:
    item = ComparisonRepository(session).get(comparison_id)
    if item is None:
        raise HTTPException(status_code=404, detail="engine comparison not found")
    return _serialize(item)
