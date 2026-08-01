from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import SelectionSnapshot


class ImmutableSnapshotError(RuntimeError):
    pass


class SelectionSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        selection_date: date,
        strategy_code: str,
        strategy_version: str,
        factor_version: str,
        data_snapshot_version: str,
        selection_status: str,
        candidates: list[dict[str, Any]],
        artifact_path: str | None,
    ) -> SelectionSnapshot:
        record = SelectionSnapshot(
            selection_date=selection_date,
            strategy_code=strategy_code,
            strategy_version=strategy_version,
            factor_version=factor_version,
            data_snapshot_version=data_snapshot_version,
            selection_status=selection_status,
            candidates_json=json.dumps(
                candidates, ensure_ascii=False, default=str, allow_nan=False
            ),
            artifact_path=artifact_path,
        )
        self.session.add(record)
        try:
            self.session.commit()
        except IntegrityError as error:
            self.session.rollback()
            raise ImmutableSnapshotError(
                "相同日期、策略版本和数据快照的候选池已存在，不允许覆盖。"
            ) from error
        self.session.refresh(record)
        return record

    def latest(self, strategy_code: str | None = None) -> SelectionSnapshot | None:
        statement = select(SelectionSnapshot)
        if strategy_code:
            statement = statement.where(SelectionSnapshot.strategy_code == strategy_code)
        return self.session.scalar(
            statement.order_by(
                desc(SelectionSnapshot.selection_date), desc(SelectionSnapshot.id)
            ).limit(1)
        )

    def list(self, limit: int = 100) -> list[SelectionSnapshot]:
        statement = (
            select(SelectionSnapshot)
            .order_by(desc(SelectionSnapshot.selection_date), desc(SelectionSnapshot.id))
            .limit(limit)
        )
        return list(self.session.scalars(statement))


def data_snapshot_version(
    datasets: Mapping[str, pd.DataFrame | None] | pd.DataFrame,
) -> str:
    """Hash every source participating in a selection, including missing sources."""

    named = {"daily": datasets} if isinstance(datasets, pd.DataFrame) else datasets
    digest = hashlib.sha256()
    for name in sorted(named):
        frame = named[name]
        digest.update(name.encode("utf-8"))
        if frame is None:
            digest.update(b"<missing>")
            continue
        stable = frame.copy()
        stable = stable.reindex(sorted(stable.columns), axis=1)
        sort_keys = [
            key for key in ("date", "timestamp", "symbol", "available_at") if key in stable
        ]
        if sort_keys:
            stable = stable.sort_values(sort_keys, kind="stable")
        stable = stable.reset_index(drop=True)
        digest.update(
            json.dumps(
                [(column, str(stable[column].dtype)) for column in stable],
                ensure_ascii=False,
            ).encode("utf-8")
        )
        hashes = np.asarray(pd.util.hash_pandas_object(stable, index=True).values)
        digest.update(hashes.tobytes())
    return f"sha256:{digest.hexdigest()}"


def write_candidate_artifact(
    candidates: list[dict[str, Any]],
    *,
    artifact_root: Path,
    selection_date: date,
    strategy_code: str,
    strategy_version: str,
    factor_version: str,
    snapshot_version: str,
) -> Path:
    short_hash = snapshot_version.split(":", maxsplit=1)[-1][:12]
    safe_strategy_version = _safe_path_segment(strategy_version)
    safe_factor_version = _safe_path_segment(factor_version)
    path = (
        artifact_root
        / "selections"
        / selection_date.isoformat()
        / strategy_code
        / f"strategy-{safe_strategy_version}"
        / f"factor-{safe_factor_version}"
        / f"data-{short_hash}.parquet"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    # Persist nested audit payloads as JSON strings. PyArrow cannot encode an empty
    # dict as a struct (common when minute confirmation is unavailable), while JSON
    # preserves the schema without inventing placeholder microstructure fields.
    rows = [
        {
            key: (
                json.dumps(value, ensure_ascii=False, default=str, allow_nan=False)
                if isinstance(value, (dict, list))
                else value
            )
            for key, value in candidate.items()
        }
        for candidate in candidates
    ]
    temporary_directory = artifact_root / ".tmp"
    temporary_directory.mkdir(parents=True, exist_ok=True)
    temporary = temporary_directory / f"{uuid.uuid4().hex}.parquet"
    try:
        pd.DataFrame(rows).to_parquet(temporary, index=False)
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise ImmutableSnapshotError(
                f"candidate artifact already exists and cannot be overwritten: {path}"
            ) from error
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _safe_path_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    if not cleaned:
        raise ValueError("version must contain a filesystem-safe character")
    return cleaned
