"""Validation and safe resolution for imported historical data snapshots.

The walk-forward service reads a manifest supplied by a caller.  Keeping the
validation here prevents each research task from inventing a different audit
gate or accidentally reading a file outside the immutable snapshot directory.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any

import pandas as pd

from app.data.contracts import POINT_IN_TIME_REQUIRED_COLUMNS, validate_market_frame


class SnapshotManifestError(ValueError):
    """The snapshot manifest cannot be trusted for a point-in-time run."""


@dataclass(frozen=True, slots=True)
class SnapshotManifest:
    """Parsed and validated manifest with paths confined to its directory."""

    path: Path
    snapshot_id: str
    immutable: bool
    audit_valid: bool
    coverage_ratio: float
    status: str
    files: dict[str, Path]
    metadata: dict[str, Any]

    def file(self, name: str, *, required: bool = True) -> Path | None:
        value = self.files.get(name)
        if value is None and required:
            raise SnapshotManifestError(f"snapshot manifest is missing {name} data")
        return value


class SnapshotManifestValidator:
    """Load and validate an imported 2018--2025 data snapshot manifest."""

    def __init__(
        self,
        *,
        minimum_coverage_ratio: float = 0.95,
        required_files: tuple[str, ...] = ("daily",),
        required_information_cutoff: str | None = "18:30",
    ) -> None:
        if not 0 <= minimum_coverage_ratio <= 1:
            raise ValueError("minimum_coverage_ratio must be between 0 and 1")
        self.minimum_coverage_ratio = minimum_coverage_ratio
        self.required_files = required_files
        self.required_information_cutoff = required_information_cutoff

    def load(self, path: str | Path) -> SnapshotManifest:
        manifest_path = Path(path).expanduser().resolve()
        if not manifest_path.exists() or not manifest_path.is_file():
            raise SnapshotManifestError(f"snapshot manifest does not exist: {manifest_path}")
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SnapshotManifestError(
                f"snapshot manifest is not valid JSON: {manifest_path}"
            ) from error
        if not isinstance(payload, dict):
            raise SnapshotManifestError("snapshot manifest root must be an object")
        return self.validate(payload, manifest_path)

    def validate(self, manifest: Mapping[str, Any], path: str | Path) -> SnapshotManifest:
        manifest_path = Path(path).expanduser().resolve()
        if not manifest_path.exists() or not manifest_path.is_file():
            raise SnapshotManifestError("snapshot manifest disappeared during validation")
        if manifest.get("immutable") is False:
            raise SnapshotManifestError("snapshot manifest is not immutable")
        status = str(manifest.get("status", "ready"))
        if status in {"invalid", "blocked", "failed"}:
            raise SnapshotManifestError("snapshot manifest failed its audit gate")
        if manifest.get("audit_valid") is False:
            raise SnapshotManifestError("snapshot manifest failed its audit gate")
        coverage_value = manifest.get(
            "coverage_ratio",
            manifest.get("daily_coverage_ratio", manifest.get("daily_coverage", 1.0)),
        )
        try:
            coverage_ratio = float(coverage_value)
        except (TypeError, ValueError) as error:
            raise SnapshotManifestError("snapshot coverage ratio is not numeric") from error
        if not 0 <= coverage_ratio <= 1:
            raise SnapshotManifestError("snapshot coverage ratio must be between 0 and 1")
        if coverage_ratio < self.minimum_coverage_ratio:
            raise SnapshotManifestError(
                f"daily coverage ratio {coverage_ratio:.2%} is below "
                f"{self.minimum_coverage_ratio:.2%}"
            )
        if self.required_information_cutoff is not None:
            cutoff = manifest.get("information_cutoff", manifest.get("point_in_time_cutoff"))
            if cutoff is not None and _normalise_cutoff(str(cutoff)) != _normalise_cutoff(
                self.required_information_cutoff
            ):
                raise SnapshotManifestError(
                    f"snapshot information cutoff must be {self.required_information_cutoff}"
                )

        raw_snapshot_id = manifest.get("snapshot_id", manifest.get("version", ""))
        snapshot_id = str(raw_snapshot_id).strip() if raw_snapshot_id is not None else ""
        if not snapshot_id:
            snapshot_id = manifest_path.parent.name
        if not snapshot_id:
            raise SnapshotManifestError("snapshot manifest has no snapshot_id or version")
        files = self._resolve_files(manifest, manifest_path)
        for name in self.required_files:
            if name not in files:
                raise SnapshotManifestError(f"snapshot manifest is missing {name} data")
        return SnapshotManifest(
            path=manifest_path,
            snapshot_id=snapshot_id,
            immutable=manifest.get("immutable", True) is not False,
            audit_valid=manifest.get("audit_valid", True) is not False,
            coverage_ratio=coverage_ratio,
            status=status,
            files=files,
            metadata=dict(manifest),
        )

    @staticmethod
    def _resolve_files(manifest: Mapping[str, Any], manifest_path: Path) -> dict[str, Path]:
        values: dict[str, Any] = {}
        nested = manifest.get("files", {})
        if isinstance(nested, Mapping):
            values.update({str(key): value for key, value in nested.items()})
        for name, value in manifest.items():
            if name.endswith("_path"):
                values.setdefault(name.removesuffix("_path"), value)
        resolved: dict[str, Path] = {}
        root = manifest_path.parent.resolve()
        for name, value in values.items():
            if isinstance(value, Mapping):
                value = value.get("path")
            if value is None:
                continue
            candidate = Path(str(value)).expanduser()
            if not candidate.is_absolute():
                candidate = root / candidate
            candidate = candidate.resolve()
            if not candidate.is_relative_to(root):
                raise SnapshotManifestError(
                    f"snapshot file {name!r} is outside the manifest directory"
                )
            if not candidate.exists() or not candidate.is_file():
                raise SnapshotManifestError(f"snapshot file does not exist: {candidate}")
            resolved[str(name)] = candidate
        return resolved


def validate_snapshot_manifest(
    path: str | Path,
    *,
    minimum_coverage_ratio: float = 0.95,
    required_files: tuple[str, ...] = ("daily",),
    required_information_cutoff: str | None = "18:30",
) -> SnapshotManifest:
    """Convenience wrapper used by services and command-line experiments."""

    return SnapshotManifestValidator(
        minimum_coverage_ratio=minimum_coverage_ratio,
        required_files=required_files,
        required_information_cutoff=required_information_cutoff,
    ).load(path)


def audit_snapshot_files(
    snapshot: SnapshotManifest,
    *,
    minimum_coverage_ratio: float = 0.95,
) -> dict[str, Any]:
    """Audit real files referenced by a manifest before a research run.

    Manifest metadata is not trusted as a substitute for checking the bars.  A
    caller may use a tiny placeholder file in a unit test and skip this helper;
    production services call it after ``load`` and therefore receive a clear
    block for malformed, duplicate, stale, or under-covered data.
    """

    daily_path = snapshot.file("daily")
    assert daily_path is not None
    daily = _read_tabular(daily_path)
    quality = validate_market_frame(daily, granularity="daily")
    if not quality.valid:
        raise SnapshotManifestError(
            "daily market data failed quality audit: "
            f"missing={quality.missing_columns}, duplicates={quality.duplicate_rows}, "
            f"invalid_prices={quality.invalid_price_rows}"
        )
    expected = int(snapshot.metadata.get("expected_universe_size", 0) or 0)
    observed = daily.assign(date=pd.to_datetime(daily["date"], errors="coerce"))
    if observed["date"].isna().any():
        raise SnapshotManifestError("daily market data contains invalid dates")
    universe_path = snapshot.file("universe", required=False)
    universe = _read_tabular(universe_path) if universe_path is not None else None
    suspensions_path = snapshot.file("suspensions", required=False)
    suspensions = _read_tabular(suspensions_path) if suspensions_path is not None else None
    suspended_by_date: dict[pd.Timestamp, set[str]] = {}
    if suspensions is not None and {"date", "symbol"}.issubset(suspensions.columns):
        suspension_dates = pd.to_datetime(suspensions["date"], format="mixed", errors="coerce")
        for day, symbol in zip(suspension_dates, suspensions["symbol"], strict=False):
            if pd.notna(day):
                suspended_by_date.setdefault(pd.Timestamp(day).normalize(), set()).add(str(symbol))
    if universe is not None and {"symbol", "list_date"}.issubset(universe.columns):
        universe = universe.copy()
        universe["list_date"] = pd.to_datetime(
            universe["list_date"], format="mixed", errors="coerce"
        )
        if "delist_date" not in universe:
            universe["delist_date"] = pd.NaT
        universe["delist_date"] = pd.to_datetime(
            universe["delist_date"], format="mixed", errors="coerce"
        )
        if "last_observed_date" in universe:
            # The purchased archive has no explicit delist field.  Treat a
            # symbol's last observed bar as an inferred end date only for the
            # coverage denominator; the exact external stock_basic date, when
            # present, always takes precedence.
            inferred = pd.to_datetime(
                universe["last_observed_date"], format="mixed", errors="coerce"
            )
            universe["delist_date"] = universe["delist_date"].fillna(inferred)
        expected_by_date: dict[pd.Timestamp, int] = {}
        for current_date in sorted(observed["date"].dt.normalize().unique()):
            active = universe.loc[universe["list_date"].le(current_date)]
            if "delist_date" in active:
                active = active.loc[
                    active["delist_date"].isna() | active["delist_date"].ge(current_date)
                ]
            expected_by_date[pd.Timestamp(current_date)] = int(
                active["symbol"].astype(str).nunique()
            )
        observed_by_date = {
            pd.Timestamp(day).normalize(): set(group["symbol"].astype(str))
            for day, group in observed.groupby(observed["date"].dt.normalize())
        }
        ratios = []
        for day in expected_by_date:
            expected_symbols = set(
                universe.loc[
                    universe["list_date"].le(day)
                    & (universe["delist_date"].isna() | universe["delist_date"].ge(day)),
                    "symbol",
                ].astype(str)
            )
            suspended = suspended_by_date.get(pd.Timestamp(day).normalize(), set())
            tradable_expected = expected_symbols.difference(suspended)
            effective_expected = len(tradable_expected)
            if effective_expected:
                actual_symbols = observed_by_date.get(pd.Timestamp(day).normalize(), set())
                observed_count = len(actual_symbols.intersection(tradable_expected))
                ratios.append(float(observed_count / effective_expected))
        coverage = float(min(ratios)) if ratios else 0.0
    elif expected > 0:
        coverage = float(
            observed.groupby(observed["date"].dt.normalize())["symbol"].nunique().mean() / expected
        )
    else:
        coverage = float(snapshot.coverage_ratio)
    if coverage < minimum_coverage_ratio:
        raise SnapshotManifestError(
            f"daily coverage ratio {coverage:.2%} is below {minimum_coverage_ratio:.2%}"
        )
    for name in ("financials", "valuations"):
        path = snapshot.file(name, required=False)
        if path is not None:
            validate_point_in_time_path(path, name=name)
    hashes = snapshot.metadata.get("content_hashes", {})
    file_specs = snapshot.metadata.get("files", {})
    if isinstance(file_specs, Mapping):
        for name, spec in file_specs.items():
            if isinstance(spec, Mapping):
                expected = spec.get("sha256", spec.get("content_hash"))
                if expected:
                    hashes = (
                        {**hashes, str(name): expected}
                        if isinstance(hashes, Mapping)
                        else {str(name): expected}
                    )
    if isinstance(hashes, Mapping):
        for name, expected_hash in hashes.items():
            path = snapshot.file(str(name), required=False)
            if path is None:
                continue
            actual = _sha256(path)
            if str(expected_hash).lower().replace("sha256:", "") != actual:
                raise SnapshotManifestError(f"content hash mismatch for {name}")
    return {
        "daily_row_count": len(daily),
        "daily_symbol_count": int(daily["symbol"].nunique()),
        "daily_min_date": observed["date"].min().date().isoformat(),
        "daily_max_date": observed["date"].max().date().isoformat(),
        "daily_coverage_ratio": coverage,
        "audit_valid": True,
    }


def _read_tabular(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() in {".csv", ".tsv"}:
        return pd.read_csv(path, sep="\t" if path.suffix.lower() == ".tsv" else ",")
    raise SnapshotManifestError(f"unsupported snapshot file format: {path.suffix}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_point_in_time_frame(
    frame: pd.DataFrame,
    *,
    name: str = "point-in-time dataset",
    cutoff: time = time(18, 30),
) -> None:
    """Validate audit fields and prevent records from being available after cutoff.

    A source may publish a record after the market close.  The record is still
    valid for a later signal date, but it must never be treated as known before
    the stated after-close cutoff on its publication date.
    """

    missing = POINT_IN_TIME_REQUIRED_COLUMNS.difference(frame.columns)
    if missing:
        raise SnapshotManifestError(
            f"{name} is missing point-in-time audit columns: {sorted(missing)}"
        )
    available = pd.to_datetime(frame["available_at"], format="mixed", errors="coerce")
    if available.isna().any():
        raise SnapshotManifestError(f"{name}.available_at contains invalid timestamps")
    if (frame["available_at"].astype(str).str.strip() == "").any():
        raise SnapshotManifestError(f"{name}.available_at contains empty timestamps")
    published = pd.to_datetime(frame["published_at"], format="mixed", errors="coerce")
    if published.isna().any():
        raise SnapshotManifestError(f"{name}.published_at contains invalid timestamps")
    if (available < published).any():
        raise SnapshotManifestError(f"{name} contains available_at earlier than published_at")
    # A source's availability time is validated against its own date only when
    # period_end is date-like; this catches malformed future leakage metadata
    # without assuming a particular reporting calendar.
    period_end = pd.to_datetime(frame["period_end"], format="mixed", errors="coerce")
    if period_end.isna().any():
        raise SnapshotManifestError(f"{name}.period_end contains invalid dates")
    cutoff_delta = pd.Timedelta(hours=cutoff.hour, minutes=cutoff.minute, seconds=cutoff.second)
    # Do not reject records published after a period end: they are expected for
    # financial disclosures.  The explicit cutoff is recorded for callers to
    # use in as-of joins (the calculator applies it at each market date).
    _ = cutoff_delta


def validate_point_in_time_path(
    path: str | Path,
    *,
    name: str = "point-in-time dataset",
    cutoff: time = time(18, 30),
    batch_size: int = 100_000,
) -> None:
    """Validate a large PIT Parquet incrementally instead of loading it twice."""

    resolved = Path(path)
    if resolved.suffix.lower() != ".parquet":
        validate_point_in_time_frame(_read_tabular(resolved), name=name, cutoff=cutoff)
        return
    import pyarrow.parquet as parquet  # type: ignore[import-untyped]

    parquet_file = parquet.ParquetFile(resolved)
    missing = POINT_IN_TIME_REQUIRED_COLUMNS.difference(parquet_file.schema_arrow.names)
    if missing:
        raise SnapshotManifestError(
            f"{name} is missing point-in-time audit columns: {sorted(missing)}"
        )
    columns = sorted(POINT_IN_TIME_REQUIRED_COLUMNS)
    for batch in parquet_file.iter_batches(batch_size=batch_size, columns=columns):
        validate_point_in_time_frame(batch.to_pandas(), name=name, cutoff=cutoff)


def _normalise_cutoff(value: str) -> str:
    text = value.strip()
    if len(text) == 5 and text[2] == ":":
        return f"{text}:00"
    return text
