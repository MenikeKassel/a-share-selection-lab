from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from app.market_rules.price_limits import price_limit_ratio


class PurchasedCsvImportError(RuntimeError):
    """The purchased archive cannot be imported safely."""


@dataclass(frozen=True, slots=True)
class PurchasedCsvSnapshotResult:
    snapshot_id: str
    manifest_path: Path
    daily_path: Path
    source_file_count: int
    row_count: int
    symbol_count: int
    min_date: date
    max_date: date
    source_sha256: str


_REQUIRED_COLUMNS = {
    "ts_code",
    "trade_date",
    "name",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "vol",
    "amount",
    "adj_factor",
    "first_adj",
}
_USECOLS = sorted(
    _REQUIRED_COLUMNS
    | {
        "change",
        "pct_chg",
    }
)
_ST_RE = re.compile(r"(?:^|[^\u4e00-\u9fff])(\*?ST|S\*ST|退)(?:[^\u4e00-\u9fff]|$)|ST|退")


class PurchasedCsvSnapshotImporter:
    """Convert one immutable purchased archive into the system market schema.

    The source directory is never modified.  A staging directory is populated
    first and atomically renamed into the requested snapshot directory only
    after all CSV files pass the same header and row-level checks.
    """

    def __init__(
        self,
        *,
        source_dir: str | Path,
        snapshot_root: str | Path,
        snapshot_id: str = "ashare-2018-2025-v1",
        start_date: date = date(2016, 1, 1),
        end_date: date = date(2025, 12, 31),
        chunk_size: int = 100_000,
    ) -> None:
        self.source_dir = Path(source_dir).expanduser().resolve()
        self.snapshot_root = Path(snapshot_root).expanduser().resolve()
        self.snapshot_id = snapshot_id
        self.start_date = start_date
        self.end_date = end_date
        self.chunk_size = chunk_size
        if start_date > end_date:
            raise ValueError("start_date must not be after end_date")
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")

    def run(self) -> PurchasedCsvSnapshotResult:
        self._validate_source()
        destination = self.snapshot_root / self.snapshot_id
        if destination.exists():
            raise PurchasedCsvImportError(
                f"snapshot already exists and is immutable: {destination}"
            )
        self.snapshot_root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{self.snapshot_id}-", dir=self.snapshot_root))
        try:
            result = self._write_staging(staging)
            staging.replace(destination)
            return PurchasedCsvSnapshotResult(
                snapshot_id=result["snapshot_id"],
                manifest_path=destination / "manifest.json",
                daily_path=destination / "daily.parquet",
                source_file_count=result["source_file_count"],
                row_count=result["row_count"],
                symbol_count=result["symbol_count"],
                min_date=pd.Timestamp(result["min_date"]).date(),
                max_date=pd.Timestamp(result["max_date"]).date(),
                source_sha256=result["source_sha256"],
            )
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _validate_source(self) -> None:
        if not self.source_dir.exists() or not self.source_dir.is_dir():
            raise PurchasedCsvImportError(f"source directory does not exist: {self.source_dir}")
        csv_files = sorted(self.source_dir.glob("*.csv"))
        if not csv_files:
            raise PurchasedCsvImportError(f"source directory has no CSV files: {self.source_dir}")
        expected_header: set[str] | None = None
        for path in csv_files:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle)
                header = set(next(reader, []))
            if not _REQUIRED_COLUMNS.issubset(header):
                missing = sorted(_REQUIRED_COLUMNS.difference(header))
                raise PurchasedCsvImportError(f"{path.name} is missing columns: {missing}")
            if expected_header is None:
                expected_header = header
            elif header != expected_header:
                raise PurchasedCsvImportError(f"{path.name} has a different CSV header")

    def _write_staging(self, staging: Path) -> dict[str, Any]:
        daily_path = staging / "daily.parquet"
        files = sorted(self.source_dir.glob("*.csv"))
        writer: pq.ParquetWriter | None = None
        source_hash = hashlib.sha256()
        source_file_hashes: dict[str, str] = {}
        row_count = 0
        symbols: set[str] = set()
        min_date: pd.Timestamp | None = None
        max_date: pd.Timestamp | None = None
        invalid_rows = 0
        try:
            for index, path in enumerate(files, start=1):
                source_hash.update(path.name.encode("utf-8"))
                source_hash.update(str(path.stat().st_size).encode("ascii"))
                source_file_hashes[path.name] = _sha256(path)
                for frame in self._read_file(path):
                    invalid_rows += self._invalid_row_count(frame)
                    if invalid_rows:
                        raise PurchasedCsvImportError(
                            f"invalid price/date rows found in {path.name}"
                        )
                    if frame.empty:
                        continue
                    table = pa.Table.from_pandas(frame, preserve_index=False)
                    if writer is None:
                        writer = pq.ParquetWriter(
                            daily_path,
                            table.schema,
                            compression="zstd",
                            use_dictionary=["symbol", "name"],
                        )
                    writer.write_table(table, row_group_size=min(len(frame), self.chunk_size))
                    row_count += len(frame)
                    symbols.update(frame["symbol"].astype(str).unique())
                    chunk_min = frame["date"].min()
                    chunk_max = frame["date"].max()
                    min_date = chunk_min if min_date is None else min(min_date, chunk_min)
                    max_date = chunk_max if max_date is None else max(max_date, chunk_max)
                if index % 250 == 0:
                    # Keep the importer usable from terminals without emitting data.
                    print(f"imported {index}/{len(files)} purchased CSV files")
        finally:
            if writer is not None:
                writer.close()
        if writer is None or min_date is None or max_date is None:
            raise PurchasedCsvImportError("the requested date window contains no daily rows")
        if min_date.date() > self.start_date + timedelta(
            days=7
        ) or max_date.date() < self.end_date - timedelta(days=7):
            raise PurchasedCsvImportError(
                f"date coverage is {min_date.date()}..{max_date.date()}, "
                f"need {self.start_date}..{self.end_date}"
            )
        content_hash = _sha256(daily_path)
        universe_path = staging / "universe.parquet"
        universe = pd.read_parquet(daily_path, columns=["symbol", "date"])
        universe = (
            universe.assign(date=pd.to_datetime(universe["date"], errors="coerce"))
            .groupby("symbol", as_index=False)
            .agg(list_date=("date", "min"), last_observed_date=("date", "max"))
        )
        universe.to_parquet(universe_path, index=False, compression="zstd")
        universe_hash = _sha256(universe_path)
        manifest = {
            "snapshot_id": self.snapshot_id,
            "version": self.snapshot_id,
            "status": "staged",
            "immutable": True,
            "audit_valid": False,
            "coverage_ratio": 0.0,
            "daily_coverage_ratio": 0.0,
            "information_cutoff": "18:30:00",
            "point_in_time_cutoff": "18:30:00",
            "source": {
                "type": "purchased_csv_archive",
                "path": str(self.source_dir),
                "read_only": True,
                "file_count": len(files),
                "source_listing_sha256": source_hash.hexdigest(),
                "file_hashes": source_file_hashes,
                "units": {"vol": "lots", "amount": "thousand_cny"},
            },
            "date_range": {
                "warmup_start": self.start_date.isoformat(),
                "validation_start": "2018-01-01",
                "validation_end": "2025-12-31",
                "source_max_date": max_date.date().isoformat(),
            },
            "expected_universe_size": len(universe),
            "row_count": row_count,
            "symbol_count": len(symbols),
            "files": {
                "daily": {"path": "daily.parquet", "sha256": content_hash},
                "universe": {"path": "universe.parquet", "sha256": universe_hash},
            },
            "content_hashes": {"daily": content_hash, "universe": universe_hash},
            "price_conventions": {
                "execution_ohlc": "raw",
                "factor_ohlc": "causal_hfq_reconstructed_from_adj_factor",
                "qfq_used": False,
            },
            "microstructure": {
                "cvd": "unavailable",
                "bid_ask_delta": "unavailable",
                "footprint": "unavailable",
                "absorption": "unavailable",
                "iceberg_order": "unavailable",
                "level2_orderbook": "unavailable",
                "option_wall": "unavailable",
            },
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {
            "snapshot_id": self.snapshot_id,
            "source_file_count": len(files),
            "row_count": row_count,
            "symbol_count": len(symbols),
            "min_date": min_date,
            "max_date": max_date,
            "source_sha256": source_hash.hexdigest(),
        }

    def _read_file(self, path: Path) -> Iterator[pd.DataFrame]:
        try:
            first_row = pd.read_csv(path, usecols=["trade_date"], nrows=1, dtype="string")
            file_start = pd.to_datetime(
                first_row["trade_date"].iloc[0], format="%Y%m%d", errors="raise"
            ).normalize()
        except (OSError, ValueError, IndexError, pd.errors.ParserError) as error:
            raise PurchasedCsvImportError(f"cannot read first date from {path}: {error}") from error
        try:
            iterator = pd.read_csv(
                path,
                usecols=_USECOLS,
                chunksize=self.chunk_size,
                dtype={"ts_code": "string", "name": "string"},
            )
        except (OSError, ValueError, pd.errors.ParserError) as error:
            raise PurchasedCsvImportError(f"cannot read {path}: {error}") from error
        for frame in iterator:
            dates = pd.to_datetime(
                frame["trade_date"].astype(str), format="%Y%m%d", errors="coerce"
            )
            frame = frame.loc[
                dates.between(pd.Timestamp(self.start_date), pd.Timestamp(self.end_date))
            ].copy()
            if frame.empty:
                continue
            frame["date"] = dates.loc[frame.index].dt.normalize()
            frame["symbol"] = frame["ts_code"].astype(str)
            for column in (
                "open",
                "high",
                "low",
                "close",
                "pre_close",
                "change",
                "pct_chg",
                "adj_factor",
                "first_adj",
            ):
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
            frame["volume"] = pd.to_numeric(frame["vol"], errors="coerce") * 100.0
            frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce") * 1000.0
            first_adj = pd.to_numeric(frame["first_adj"], errors="coerce").replace(0, np.nan)
            factor = pd.to_numeric(frame["adj_factor"], errors="coerce")
            factor = factor.where(factor > 0, 1.0)
            scale = (factor / first_adj).replace([np.inf, -np.inf], np.nan).fillna(1.0)
            for column in ("open", "high", "low", "close", "pre_close"):
                frame[f"adj_{column}"] = frame[column] * scale
            frame["name"] = frame["name"].fillna("").astype(str)
            frame["is_st"] = frame["name"].map(lambda value: bool(_ST_RE.search(value)))
            frame["delisting_risk"] = frame["name"].str.contains("退", regex=False, na=False)
            frame["suspended"] = frame["volume"].fillna(0).le(0)
            # PR 2: close-time limit flags come from the versioned rule table;
            # open-time flags only know the open price.  close_* fields are
            # close-time information and MUST NOT gate next-open fills.
            frame["close_at_limit_up"], frame["close_at_limit_down"] = _derive_limit_flags(
                frame
            )
            frame["one_word_limit_up"] = frame["close_at_limit_up"] & _one_word(frame)
            frame["one_word_limit_down"] = frame["close_at_limit_down"] & _one_word(frame)
            open_limit_ratio = frame.apply(
                lambda row: float(
                    price_limit_ratio(
                        str(row["symbol"]),
                        pd.Timestamp(row["date"]).date(),
                        is_st=bool(row.get("is_st", False)),
                    )
                ),
                axis=1,
            )
            pre_close = frame["pre_close"].replace(0, np.nan)
            tolerance = pre_close * 0.0015
            open_limit_up = pre_close * (1.0 + open_limit_ratio)
            open_limit_down = pre_close * (1.0 - open_limit_ratio)
            frame["open_at_limit_up"] = (
                frame["open"].fillna(np.nan) >= open_limit_up - tolerance
            )
            frame["open_at_limit_down"] = (
                frame["open"].fillna(np.nan) <= open_limit_down + tolerance
            )
            frame["price_limit_rule_version"] = "a_share_daily_v2"
            # Deprecated aliases retained for downstream compat; the v2
            # engine ignores them for open-time decisions.
            frame["limit_up"] = frame["close_at_limit_up"]
            frame["limit_down"] = frame["close_at_limit_down"]
            frame["limit_source"] = "derived_from_raw_daily"
            frame["has_minute_data"] = False
            frame["listing_days"] = (frame["date"] - file_start).dt.days.astype(float)
            yield frame[
                [
                    "date",
                    "symbol",
                    "name",
                    "open",
                    "high",
                    "low",
                    "close",
                    "pre_close",
                    "change",
                    "pct_chg",
                    "volume",
                    "amount",
                    "adj_factor",
                    "first_adj",
                    "adj_open",
                    "adj_high",
                    "adj_low",
                    "adj_close",
                    "adj_pre_close",
                    "is_st",
                    "delisting_risk",
                    "suspended",
                    "close_at_limit_up",
                    "close_at_limit_down",
                    "open_at_limit_up",
                    "open_at_limit_down",
                    "one_word_limit_up",
                    "one_word_limit_down",
                    "price_limit_rule_version",
                    "limit_up",
                    "limit_down",
                    "limit_source",
                    "has_minute_data",
                    "listing_days",
                ]
            ]

    @staticmethod
    def _invalid_row_count(frame: pd.DataFrame) -> int:
        if frame.empty:
            return 0
        prices = frame[["open", "high", "low", "close"]]
        invalid = prices.isna().any(axis=1) | (prices <= 0).any(axis=1)
        invalid |= frame["high"] < prices.max(axis=1)
        invalid |= frame["low"] > prices.min(axis=1)
        return int(invalid.sum())


def _derive_limit_flags(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Close-at-limit flags from the versioned rule table (PR 2.4).

    Deprecated legacy signature kept for import-time callers; the rule
    lookup now lives in ``app.market_rules.price_limits``.
    """
    from app.market_rules.price_limits import derive_limit_flags

    return derive_limit_flags(frame)


def _one_word(frame: pd.DataFrame) -> pd.Series:
    return frame[["open", "high", "low", "close"]].nunique(axis=1).eq(1)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
