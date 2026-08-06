from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from app.adapters.market_data.tinyshare.client import (
    TinyShareCapability,
    TinyShareIsolatedClient,
    TinyShareProviderError,
)


class TinyShareSupplementError(RuntimeError):
    """Supplementary snapshot creation failed its capability contract."""


VALUATION_STORAGE_COLUMNS = [
    "symbol",
    "date",
    "period_end",
    "published_at",
    "available_at",
    "available_at_precision",
    "fetched_at",
    "source",
    "content_hash",
    "pe_ttm",
    "pb",
    "ps_ttm",
    "dividend_yield",
    "free_cashflow_yield",
    "market_cap",
    "turnover_rate",
    "volume_ratio",
]


REQUIRED_CAPABILITIES: tuple[dict[str, Any], ...] = (
    {
        "name": "trading_calendar",
        "method": "trade_cal",
        "required": True,
        "params": {
            "exchange": "SSE",
            "start_date": "20180101",
            "end_date": "20180110",
            "is_open": "1",
        },
    },
    {
        "name": "stock_basic_all_statuses",
        "method": "stock_basic",
        "required": True,
        "params": {
            "exchange": "",
            "list_status": "",
            "fields": "ts_code,symbol,name,market,list_date,delist_date",
        },
    },
    {
        "name": "csi300_daily",
        "method": "index_daily",
        "required": True,
        "params": {"ts_code": "000300.SH", "start_date": "20180101", "end_date": "20180110"},
    },
    {
        "name": "historical_industry_membership",
        "method": "index_member_all",
        "required": True,
        "params": {"is_new": "N"},
    },
    {
        "name": "daily_valuation",
        "method": "daily_basic",
        "required": True,
        "params": {"trade_date": "20180102", "fields": "ts_code,trade_date,pe_ttm"},
    },
    {
        "name": "financial_indicators_vip",
        "method": "fina_indicator_vip",
        "required": True,
        "params": {"period": "20171231", "fields": "ts_code,end_date,ann_date"},
    },
    {
        "name": "income_vip",
        "method": "income_vip",
        "required": True,
        "params": {
            "period": "20171231",
            "fields": "ts_code,end_date,ann_date,f_ann_date",
        },
    },
    {
        "name": "cashflow_vip",
        "method": "cashflow_vip",
        "required": True,
        "params": {
            "period": "20171231",
            "fields": "ts_code,end_date,ann_date,f_ann_date",
        },
    },
    {
        "name": "balancesheet_vip",
        "method": "balancesheet_vip",
        "required": True,
        "params": {
            "period": "20171231",
            "fields": "ts_code,end_date,ann_date,f_ann_date",
        },
    },
    {
        "name": "historical_st",
        "method": "stock_st",
        "required": True,
        "params": {"trade_date": "20180102"},
    },
)

OPTIONAL_CAPABILITIES: tuple[dict[str, Any], ...] = (
    {
        "name": "daily_limit_prices",
        "method": "stk_limit",
        "required": False,
        "params": {"trade_date": "20180102"},
    },
    {
        "name": "daily_suspensions",
        "method": "suspend_d",
        "required": False,
        "params": {"trade_date": "20180102"},
    },
)


class TinyShareSnapshotCompleter:
    """Fetch only missing datasets and finalize a staged purchased snapshot."""

    def __init__(self, client: TinyShareIsolatedClient) -> None:
        self.client = client

    def probe(self) -> list[TinyShareCapability]:
        capabilities = self.client.probe([*REQUIRED_CAPABILITIES, *OPTIONAL_CAPABILITIES])
        expected_columns = {
            "trading_calendar": {"cal_date", "is_open"},
            "stock_basic_all_statuses": {"ts_code", "symbol", "list_date"},
            "csi300_daily": {"trade_date", "close"},
            "daily_valuation": {"ts_code", "trade_date"},
            "financial_indicators_vip": {"ts_code", "end_date"},
            "income_vip": {"ts_code", "end_date"},
            "cashflow_vip": {"ts_code", "end_date"},
            "balancesheet_vip": {"ts_code", "end_date"},
            "historical_st": {"ts_code", "trade_date"},
            "daily_limit_prices": {"ts_code", "trade_date", "up_limit", "down_limit"},
            "daily_suspensions": {"ts_code", "trade_date"},
        }
        alternative_columns = {
            "historical_industry_membership": (
                {"ts_code", "in_date"},
                {"con_code", "in_date"},
            )
        }
        checked: list[TinyShareCapability] = []
        for item in capabilities:
            required = expected_columns.get(item.name)
            alternatives = alternative_columns.get(item.name, ())
            if (
                item.available
                and (required or alternatives)
                and item.row_count == 0
                and item.required
            ):
                item = TinyShareCapability(
                    item.name,
                    item.method,
                    item.required,
                    False,
                    error="probe returned no rows",
                )
            elif item.available and (required or alternatives):
                missing: list[str]
                if alternatives:
                    if any(option.issubset(item.columns) for option in alternatives):
                        checked.append(item)
                        continue
                    missing = [
                        "one of: " + ", ".join(sorted(option)) for option in alternatives
                    ]
                else:
                    assert required is not None
                    missing = sorted(required.difference(item.columns))
                if missing:
                    item = TinyShareCapability(
                        item.name,
                        item.method,
                        item.required,
                        False,
                        row_count=item.row_count,
                        columns=item.columns,
                        error=f"probe response is missing columns: {missing}",
                    )
            checked.append(item)
        return checked

    def complete(
        self,
        snapshot_dir: str | Path,
        *,
        start_date: date = date(2016, 1, 1),
        end_date: date = date(2025, 12, 31),
    ) -> dict[str, Any]:
        root = Path(snapshot_dir).expanduser().resolve()
        manifest_path = root / "manifest.json"
        if not manifest_path.exists():
            raise TinyShareSupplementError(f"snapshot manifest does not exist: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") not in {"staged", "supplemented"}:
            raise TinyShareSupplementError("only a staged snapshot may be supplemented")
        capabilities = self.probe()
        try:
            package_info: dict[str, Any] = self.client.package_info()
        except TinyShareProviderError as error:
            package_info = {"available": False, "error": str(error)}
        capability_report = {
            "provider": "tinyshare",
            "checked_at": datetime.now().astimezone().isoformat(),
            "package": package_info,
            "capabilities": [item.as_dict() for item in capabilities],
            "required_ready": all(item.available for item in capabilities if item.required),
            "coverage_ready": any(
                item.available and item.name == "daily_suspensions" for item in capabilities
            ),
            "token_recorded": False,
        }
        (root / "tinyshare_capabilities.json").write_text(
            json.dumps(capability_report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if not capability_report["required_ready"] or not capability_report["coverage_ready"]:
            # Keep the immutable input snapshot staged so a later, corrected
            # isolated-provider setup can retry supplementation.  The
            # capability report still blocks validation because audit_valid is
            # false and no formal run may consume a staged snapshot.
            manifest["status"] = "staged"
            manifest["audit_valid"] = False
            manifest["supplement"] = {**capability_report, "status": "blocked"}
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            raise TinyShareSupplementError(
                "one or more required TinyShare capabilities are unavailable"
            )

        calendar = self._fetch_calendar(start_date, end_date)
        calendar_path = _write_frame(root / "trading_calendar.parquet", calendar)
        stock_basic = self._fetch_all(
            "stock_basic",
            {
                "exchange": "",
                "list_status": "",
                "fields": "ts_code,symbol,name,market,list_date,delist_date",
            },
        )
        universe_path = _write_frame(
            root / "universe.parquet", _normalise_universe(stock_basic)
        )
        benchmark = self._fetch_benchmark(start_date, end_date)
        benchmark_path = _write_frame(
            root / "benchmark.parquet", _normalise_benchmark(benchmark)
        )
        industry_members = self._fetch_all("index_member_all", {"is_new": "N"})
        industry_path = _write_frame(
            root / "industry.parquet", _normalise_industry(industry_members)
        )
        valuations_path = self._write_valuations(
            root / "valuations.parquet", calendar, start_date=start_date, end_date=end_date
        )
        financials_path = root / "financials.parquet"
        if not financials_path.exists() or financials_path.stat().st_size == 0:
            financials = self._fetch_financials(start_date, end_date)
            financials_path = _write_frame(
                financials_path, _normalise_financials(financials, calendar)
            )
        st_rows = self._fetch_yearly(
            "stock_st",
            start_date,
            end_date,
            fields="ts_code,trade_date,name,type,type_name",
        )
        suspensions_path = root / "suspensions.parquet"
        if not suspensions_path.exists() or suspensions_path.stat().st_size == 0:
            suspensions = self._fetch_yearly(
                "suspend_d",
                start_date,
                end_date,
                fields="ts_code,trade_date,suspend_type,suspend_timing",
            )
            suspensions_path = _write_frame(
                suspensions_path, _normalise_suspensions(suspensions)
            )
        state_path = _write_frame(
            root / "state_history.parquet",
            _normalise_state(stock_basic, industry_members, st_rows, calendar=calendar),
        )

        files = {
            "trading_calendar": calendar_path,
            "universe": universe_path,
            "benchmark": benchmark_path,
            "state_history": state_path,
            "industry": industry_path,
            "valuations": valuations_path,
            "financials": financials_path,
            "suspensions": suspensions_path,
        }
        manifest["status"] = "ready"
        manifest["audit_valid"] = True
        manifest["coverage_ratio"] = 1.0
        manifest["daily_coverage_ratio"] = 1.0
        manifest["supplement"] = capability_report
        manifest["files"] = {
            **dict(manifest.get("files", {})),
            **{name: {"path": path.name, "sha256": _sha256(path)} for name, path in files.items()},
            "tinyshare_capabilities": {
                "path": "tinyshare_capabilities.json",
                "sha256": _sha256(root / "tinyshare_capabilities.json"),
            },
        }
        manifest["content_hashes"] = {
            name: spec["sha256"] for name, spec in manifest["files"].items()
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {
            "manifest_path": str(manifest_path),
            "files": {key: str(value) for key, value in files.items()},
            "capabilities": capability_report,
        }

    def _fetch_calendar(self, start_date: date, end_date: date) -> pd.DataFrame:
        rows = self._fetch_all(
            "trade_cal",
            {"exchange": "SSE", "start_date": _ymd(start_date), "end_date": _ymd(end_date)},
        )
        frame = pd.DataFrame(rows)
        if frame.empty:
            raise TinyShareSupplementError("TinyShare returned an empty trading calendar")
        frame["date"] = pd.to_datetime(frame["cal_date"], format="%Y%m%d", errors="coerce")
        if "is_open" not in frame:
            frame["is_open"] = 1
        frame["is_open"] = pd.to_numeric(frame["is_open"], errors="coerce").fillna(0).astype(bool)
        return frame[["date", "is_open"]].drop_duplicates().sort_values("date")

    def _fetch_benchmark(self, start_date: date, end_date: date) -> list[dict[str, Any]]:
        params = {
            "ts_code": "000300.SH",
            "start_date": _ymd(start_date),
            "end_date": _ymd(end_date),
        }
        try:
            return self._fetch_all("index_daily", params)
        except (TinyShareProviderError, TinyShareSupplementError):
            return self._fetch_all("index_daily", {**params, "ts_code": "399300.SZ"})

    def _fetch_financials(self, start_date: date, end_date: date) -> list[dict[str, Any]]:
        periods = pd.date_range(start_date, end_date, freq="QE")
        fields = {
            "fina_indicator_vip": (
                "ts_code,ann_date,end_date,or_yoy,tr_yoy,netprofit_yoy,"
                "dt_netprofit_yoy,roe,grossprofit_margin,fcff,debt_to_assets,extra_item"
            ),
            "income_vip": (
                "ts_code,ann_date,f_ann_date,end_date,n_income_attr_p,revenue,"
                "total_revenue,non_oper_income,non_oper_exp"
            ),
            "cashflow_vip": (
                "ts_code,ann_date,f_ann_date,end_date,free_cashflow,n_cashflow_act,net_profit"
            ),
            "balancesheet_vip": (
                "ts_code,ann_date,f_ann_date,end_date,goodwill,total_assets,inventories,"
                "accounts_receiv,total_liab"
            ),
        }
        rows: list[dict[str, Any]] = []
        for period in periods:
            period_value = period.strftime("%Y%m%d")
            try:
                indicator = self._call_resilient(
                    "fina_indicator_vip",
                    {"period": period_value, "fields": fields["fina_indicator_vip"]},
                )
                income = self._call_resilient(
                    "income_vip", {"period": period_value, "fields": fields["income_vip"]}
                )
                cashflow = self._call_resilient(
                    "cashflow_vip",
                    {"period": period_value, "fields": fields["cashflow_vip"]},
                )
                balance = self._call_resilient(
                    "balancesheet_vip",
                    {"period": period_value, "fields": fields["balancesheet_vip"]},
                )
            except TinyShareProviderError as error:
                raise TinyShareSupplementError(
                    f"financial PIT fetch failed for {period_value}: {error}"
                ) from error
            rows.extend(_merge_financial_rows(indicator, income, cashflow, balance))
        return rows

    def _call_resilient(
        self, method: str, params: dict[str, Any], *, attempts: int = 3
    ) -> list[dict[str, Any]]:
        last_error: TinyShareProviderError | None = None
        for _ in range(attempts):
            try:
                return self.client.call(method, params)
            except TinyShareProviderError as error:
                last_error = error
        raise TinyShareProviderError(
            f"{method} failed after {attempts} isolated attempts: {last_error}"
        ) from last_error

    def _write_valuations(
        self,
        path: Path,
        calendar: pd.DataFrame,
        *,
        start_date: date,
        end_date: date,
        batch_size: int = 5,
    ) -> Path:
        """Download daily valuations in persistent-worker batches and stream to Parquet."""

        open_dates = pd.to_datetime(calendar["date"], errors="coerce").dt.normalize()
        if "is_open" in calendar:
            open_dates = open_dates.loc[calendar["is_open"].fillna(False).astype(bool)]
        open_dates = open_dates.loc[
            open_dates.between(pd.Timestamp(start_date), pd.Timestamp(end_date))
        ].dropna().drop_duplicates().sort_values()
        if open_dates.empty:
            raise TinyShareSupplementError("trading calendar has no open dates for valuations")

        cache_root = path.parent / ".supplement-cache" / "valuations"
        cache_root.mkdir(parents=True, exist_ok=True)
        progress_path = path.parent / "tinyshare_valuation_progress.json"
        if path.exists() and path.stat().st_size > 0 and progress_path.exists():
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
            if progress.get("status") == "complete":
                return path
        fields = (
            "ts_code,trade_date,pe_ttm,pb,ps_ttm,dv_ttm,total_mv,"
            "turnover_rate,volume_ratio"
        )
        completed_years: list[int] = []
        parts: list[Path] = []
        for year in range(start_date.year, end_date.year + 1):
            part = cache_root / f"valuations-{year}.parquet"
            parts.append(part)
            if part.exists() and part.stat().st_size > 0:
                completed_years.append(year)
                continue
            year_dates = [
                value.strftime("%Y%m%d") for value in open_dates if int(value.year) == year
            ]
            if not year_dates:
                continue
            temporary = part.with_suffix(".parquet.tmp")
            temporary.unlink(missing_ok=True)
            writer: pq.ParquetWriter | None = None
            try:
                for offset in range(0, len(year_dates), batch_size):
                    dates = year_dates[offset : offset + batch_size]
                    requests = [
                        {"trade_date": trade_date, "fields": fields} for trade_date in dates
                    ]
                    batches = self._call_many_resilient("daily_basic", requests)
                    rows = [row for batch in batches for row in batch]
                    frame = _valuation_storage_frame(rows, calendar)
                    if frame.empty:
                        continue
                    table = pa.Table.from_pandas(frame, preserve_index=False)
                    if writer is None:
                        writer = pq.ParquetWriter(temporary, table.schema, compression="zstd")
                    writer.write_table(table, row_group_size=100_000)
                if writer is None:
                    raise TinyShareSupplementError(
                        f"TinyShare returned no daily valuations for {year}"
                    )
            finally:
                if writer is not None:
                    writer.close()
            temporary.replace(part)
            completed_years.append(year)
            progress_path.write_text(
                json.dumps(
                    {
                        "status": "downloading",
                        "completed_years": completed_years,
                        "last_completed_year": year,
                        "token_recorded": False,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        missing = [str(part) for part in parts if not part.exists()]
        if missing:
            raise TinyShareSupplementError(f"valuation cache is incomplete: {missing[:2]}")
        temporary_output = path.with_suffix(".parquet.tmp")
        temporary_output.unlink(missing_ok=True)
        output_writer: pq.ParquetWriter | None = None
        try:
            for part in parts:
                parquet = pq.ParquetFile(part)
                for batch in parquet.iter_batches(batch_size=100_000):
                    table = pa.Table.from_batches([batch])
                    if output_writer is None:
                        output_writer = pq.ParquetWriter(
                            temporary_output, table.schema, compression="zstd"
                        )
                    output_writer.write_table(table, row_group_size=100_000)
            if output_writer is None:
                raise TinyShareSupplementError("daily valuation export is empty")
        finally:
            if output_writer is not None:
                output_writer.close()
        temporary_output.replace(path)
        progress_path.write_text(
            json.dumps(
                {
                    "status": "complete",
                    "completed_years": completed_years,
                    "output_path": path.name,
                    "token_recorded": False,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return path

    def _call_many_resilient(
        self, method: str, requests: list[dict[str, Any]]
    ) -> list[list[dict[str, Any]]]:
        """Split a batch when proprietary worker limits terminate the child process."""

        try:
            return self.client.call_many(method, requests)
        except TinyShareProviderError:
            if len(requests) <= 1:
                raise
            midpoint = len(requests) // 2
            return [
                *self._call_many_resilient(method, requests[:midpoint]),
                *self._call_many_resilient(method, requests[midpoint:]),
            ]

    def _fetch_all(
        self, method: str, params: dict[str, Any], *, page_size: int = 2000
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            page_params = dict(params)
            page_params.update({"limit": page_size, "offset": offset})
            try:
                page = self._call_resilient(method, page_params)
            except TinyShareProviderError as error:
                if offset == 0:
                    # Some wrappers reject pagination parameters; retry once
                    # with the documented arguments only.
                    page = self._call_resilient(method, params)
                else:
                    raise TinyShareSupplementError(
                        f"{method} pagination failed: {error}"
                    ) from error
            if page and rows and page == rows[-len(page) :]:
                raise TinyShareSupplementError(f"{method} returned a repeated page")
            rows.extend(page)
            # Some provider wrappers ignore ``limit`` and return the complete
            # result in one call.  Treat that as a successful unpaged response
            # instead of issuing a second request with an offset the wrapper
            # will also ignore.
            if len(page) > page_size:
                break
            if len(page) < page_size:
                break
            offset += page_size
        return rows

    def _fetch_yearly(
        self, method: str, start_date: date, end_date: date, *, fields: str
    ) -> list[dict[str, Any]]:
        """Avoid provider deep-offset crashes by resetting pagination each year."""

        rows: list[dict[str, Any]] = []
        for year in range(start_date.year, end_date.year + 1):
            year_start = max(start_date, date(year, 1, 1))
            year_end = min(end_date, date(year, 12, 31))
            rows.extend(
                self._fetch_all(
                    method,
                    {
                        "start_date": _ymd(year_start),
                        "end_date": _ymd(year_end),
                        "fields": fields,
                    },
                )
            )
        return rows


def _merge_financial_rows(*datasets: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for dataset in datasets:
        for row in dataset:
            symbol = str(row.get("ts_code", row.get("symbol", "")))
            period = str(row.get("end_date", row.get("period", "")))
            if not symbol or not period:
                continue
            target = merged.setdefault((symbol, period), {})
            target.update(row)
    return list(merged.values())


def _normalise_benchmark(rows: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(
            columns=["date", "symbol", "open", "high", "low", "close", "volume", "amount"]
        )
    frame = frame.rename(columns={"trade_date": "date", "ts_code": "symbol", "vol": "volume"})
    frame["date"] = pd.to_datetime(frame["date"], format="%Y%m%d", errors="coerce")
    frame["symbol"] = frame["symbol"].fillna("000300.SH")
    frame["volume"] = pd.to_numeric(frame.get("volume", 0), errors="coerce") * 100.0
    frame["amount"] = pd.to_numeric(frame.get("amount", 0), errors="coerce") * 1000.0
    return frame[["date", "symbol", "open", "high", "low", "close", "volume", "amount"]].dropna(
        subset=["date", "close"]
    )


def _normalise_universe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=["symbol", "list_date", "delist_date", "name", "market"])
    if "ts_code" in frame:
        # TinyShare returns both a bare six-digit ``symbol`` and a suffixed
        # ``ts_code``.  The system contract uses the latter as its identifier.
        frame["symbol"] = frame["ts_code"].astype(str)
    for column in ("list_date", "delist_date"):
        if column not in frame:
            frame[column] = pd.NaT
        frame[column] = pd.to_datetime(frame[column], format="%Y%m%d", errors="coerce")
    for column in ("name", "market"):
        if column not in frame:
            frame[column] = ""
    return frame[["symbol", "list_date", "delist_date", "name", "market"]].dropna(subset=["symbol"])


def _normalise_suspensions(rows: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=["date", "symbol"])
    frame = frame.rename(columns={"trade_date": "date", "ts_code": "symbol"})
    frame["date"] = pd.to_datetime(frame["date"], format="%Y%m%d", errors="coerce")
    return frame[["date", "symbol"]].dropna().drop_duplicates()


def _normalise_valuations(rows: list[dict[str, Any]], calendar: pd.DataFrame) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame()
    frame = frame.rename(columns={"ts_code": "symbol", "trade_date": "date"})
    frame["date"] = pd.to_datetime(frame["date"], format="%Y%m%d", errors="coerce")
    frame["period_end"] = frame["date"].dt.date.astype(str)
    frame["published_at"] = frame["date"].dt.strftime("%Y-%m-%d") + "T15:30:00+08:00"
    frame["available_at"] = frame["date"].dt.strftime("%Y-%m-%d") + "T18:30:00+08:00"
    # PR 5.2: explicit precision marker survives Parquet round-trips.
    frame["available_at_precision"] = "timestamp"
    frame["fetched_at"] = datetime.now().astimezone().isoformat()
    frame["source"] = "tinyshare.daily_basic"
    hash_columns = [
        column
        for column in ("symbol", "date", "pe_ttm", "pb", "ps_ttm", "dv_ttm", "total_mv")
        if column in frame
    ]
    frame["content_hash"] = (
        pd.util.hash_pandas_object(frame[hash_columns], index=False).astype("uint64").astype(str)
    )
    if "dv_ttm" in frame:
        dividend_source = frame["dv_ttm"]
    elif "dv_ratio" in frame:
        dividend_source = frame["dv_ratio"]
    else:
        dividend_source = pd.Series(pd.NA, index=frame.index)
    frame["dividend_yield"] = pd.to_numeric(dividend_source, errors="coerce")
    frame["free_cashflow_yield"] = pd.NA
    return frame


def _valuation_storage_frame(
    rows: list[dict[str, Any]], calendar: pd.DataFrame
) -> pd.DataFrame:
    frame = _normalise_valuations(rows, calendar)
    if frame.empty:
        return pd.DataFrame(columns=VALUATION_STORAGE_COLUMNS)
    market_cap_source = (
        frame["total_mv"]
        if "total_mv" in frame
        else pd.Series(float("nan"), index=frame.index)
    )
    frame["market_cap"] = pd.to_numeric(market_cap_source, errors="coerce")
    for column in (
        "pe_ttm",
        "pb",
        "ps_ttm",
        "dividend_yield",
        "free_cashflow_yield",
        "market_cap",
        "turnover_rate",
        "volume_ratio",
    ):
        if column not in frame:
            frame[column] = float("nan")
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("float64")
    for column in (
        "symbol",
        "period_end",
        "published_at",
        "available_at",
        "fetched_at",
        "source",
        "content_hash",
    ):
        frame[column] = frame[column].astype(str)
    return frame[VALUATION_STORAGE_COLUMNS].dropna(subset=["date", "symbol"])


def _normalise_financials(rows: list[dict[str, Any]], calendar: pd.DataFrame) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame()
    frame = frame.rename(columns={"ts_code": "symbol", "end_date": "period_end"})
    frame["period_end"] = pd.to_datetime(frame["period_end"], format="%Y%m%d", errors="coerce")
    published_source = (
        frame["f_ann_date"]
        if "f_ann_date" in frame
        else frame.get("ann_date", pd.Series(pd.NaT, index=frame.index))
    )
    published = pd.to_datetime(published_source, format="%Y%m%d", errors="coerce")
    frame["published_at"] = published.dt.strftime("%Y-%m-%d") + "T18:30:00+08:00"
    frame["published_at"] = frame["published_at"].fillna(
        frame["period_end"].dt.strftime("%Y-%m-%d") + "T18:30:00+08:00"
    )
    availability_base = published.fillna(frame["period_end"])
    next_trading_dates = _next_trading_dates(availability_base, calendar)
    frame["available_at"] = next_trading_dates.dt.strftime("%Y-%m-%d") + "T18:30:00+08:00"
    # PR 5.2: explicit precision marker survives Parquet round-trips.
    frame["available_at_precision"] = "timestamp"
    frame["fetched_at"] = datetime.now().astimezone().isoformat()
    frame["source"] = "tinyshare.financial_vip"
    mapping = {
        "or_yoy": "revenue_growth_yoy",
        "tr_yoy": "revenue_growth_yoy",
        "netprofit_yoy": "net_profit_growth_yoy",
        "dt_netprofit_yoy": "deducted_profit_growth_yoy",
        "roe": "roe_ttm",
        "grossprofit_margin": "gross_margin_change",
        "ocf_to_profit": "operating_cashflow_to_profit",
        "fcff": "free_cashflow",
        "debt_to_assets": "debt_ratio",
        "goodwill": "goodwill_ratio",
        "extra_item": "non_recurring_profit_ratio",
        "n_income_attr_p": "net_profit",
    }
    for source, target in mapping.items():
        if source in frame and target not in frame:
            frame[target] = frame[source]
    frame["content_hash"] = frame.apply(_row_hash, axis=1)
    return frame


def _next_trading_dates(values: pd.Series, calendar: pd.DataFrame) -> pd.Series:
    """Return the first open date after each disclosure date."""

    if "date" not in calendar:
        return values + pd.Timedelta(days=1)
    dates = pd.to_datetime(calendar["date"], errors="coerce").dt.normalize()
    if "is_open" in calendar:
        dates = dates.loc[calendar["is_open"].fillna(False).astype(bool)]
    open_dates = pd.DatetimeIndex(dates.dropna().drop_duplicates().sort_values())
    if open_dates.empty:
        return values + pd.Timedelta(days=1)
    normalized = pd.to_datetime(values, errors="coerce").dt.normalize()
    positions = open_dates.searchsorted(normalized, side="right")
    fallback = normalized + pd.Timedelta(days=1)
    valid = positions < len(open_dates)
    output = fallback.copy()
    output.loc[valid] = pd.Series(open_dates[positions[valid]], index=output.index[valid])
    return output


def _normalise_industry(rows: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=["symbol", "effective_date", "out_date", "industry"])
    frame = frame.rename(
        columns={
            "ts_code": "symbol",
            "con_code": "symbol",
            "in_date": "effective_date",
        }
    )
    frame["effective_date"] = pd.to_datetime(
        frame["effective_date"], format="%Y%m%d", errors="coerce"
    )
    if "out_date" not in frame:
        frame["out_date"] = pd.NaT
    frame["out_date"] = pd.to_datetime(frame["out_date"], format="%Y%m%d", errors="coerce")
    if "l2_name" in frame:
        industry_source = frame["l2_name"]
    elif "l1_name" in frame:
        industry_source = frame["l1_name"]
    elif "index_name" in frame:
        industry_source = frame["index_name"]
    elif "index_code" in frame:
        industry_source = frame["index_code"]
    else:
        industry_source = pd.Series("unknown", index=frame.index)
    frame["industry"] = industry_source.fillna("unknown")
    return frame[["symbol", "effective_date", "out_date", "industry"]].dropna(
        subset=["symbol", "effective_date"]
    )


def _normalise_state(
    stock_rows: list[dict[str, Any]],
    industry_rows: list[dict[str, Any]],
    st_rows: list[dict[str, Any]],
    *,
    calendar: pd.DataFrame | None = None,
) -> pd.DataFrame:
    industry = _normalise_industry(industry_rows)
    events: list[dict[str, Any]] = []
    for row in industry.to_dict(orient="records"):
        events.append(
            {
                "symbol": row["symbol"],
                "effective_date": row["effective_date"],
                "industry": row["industry"],
            }
        )
        if pd.notna(row.get("out_date")):
            events.append(
                {"symbol": row["symbol"], "effective_date": row["out_date"], "industry": "unknown"}
            )
    basic = pd.DataFrame(stock_rows)
    if not basic.empty:
        for row in basic.to_dict(orient="records"):
            if row.get("list_date"):
                events.append(
                    {
                        "symbol": row.get("ts_code"),
                        "effective_date": pd.to_datetime(
                            str(row["list_date"]), format="%Y%m%d", errors="coerce"
                        ),
                        "list_date": pd.to_datetime(
                            str(row["list_date"]), format="%Y%m%d", errors="coerce"
                        ),
                    }
                )
            if row.get("delist_date"):
                events.append(
                    {
                        "symbol": row.get("ts_code"),
                        "effective_date": pd.to_datetime(
                            str(row["delist_date"]), format="%Y%m%d", errors="coerce"
                        ),
                        "delisting_risk": True,
                    }
                )
    st = pd.DataFrame(st_rows)
    if not st.empty:
        symbol_values = (
            st["ts_code"] if "ts_code" in st else st.get("symbol", pd.Series("", index=st.index))
        )
        date_values = (
            st["trade_date"]
            if "trade_date" in st
            else st.get("date", pd.Series(pd.NaT, index=st.index))
        )
        st["symbol"] = symbol_values.astype(str)
        st["effective_date"] = pd.to_datetime(date_values, format="%Y%m%d", errors="coerce")
        calendar_dates = (
            pd.to_datetime(calendar["date"], errors="coerce")
            .dt.normalize()
            .dropna()
            .drop_duplicates()
            .sort_values()
            .tolist()
            if calendar is not None and "date" in calendar
            else []
        )
        calendar_index = {item: index for index, item in enumerate(calendar_dates)}
        for symbol, group in st.dropna(subset=["effective_date"]).groupby("symbol", sort=False):
            dates = sorted(pd.Timestamp(item).normalize() for item in group["effective_date"])
            for current in dates:
                events.append({"symbol": symbol, "effective_date": current, "is_st": True})
            if dates and calendar_index:
                last_index = calendar_index.get(dates[-1])
                if last_index is not None and last_index + 1 < len(calendar_dates):
                    events.append(
                        {
                            "symbol": symbol,
                            "effective_date": calendar_dates[last_index + 1],
                            "is_st": False,
                        }
                    )
    result = pd.DataFrame(events)
    if result.empty:
        return pd.DataFrame(
            columns=[
                "symbol",
                "effective_date",
                "industry",
                "is_st",
                "delisting_risk",
                "list_date",
            ]
        )
    result = result.dropna(subset=["symbol", "effective_date"]).copy()
    result["symbol"] = result["symbol"].astype(str)
    result["effective_date"] = pd.to_datetime(result["effective_date"]).dt.normalize()
    result = result.sort_values(["symbol", "effective_date"])
    state_columns = [
        column
        for column in ("industry", "is_st", "delisting_risk", "list_date")
        if column in result
    ]
    if state_columns:
        result[state_columns] = result.groupby("symbol", sort=False)[state_columns].ffill()
    return (
        result.groupby(["symbol", "effective_date"], as_index=False, sort=False)
        .last()
        .sort_values(["symbol", "effective_date"])
    )


def _write_frame(path: Path, frame: pd.DataFrame) -> Path:
    if frame.empty:
        frame = pd.DataFrame({"_empty": pd.Series(dtype="int64")})
    frame.to_parquet(path, index=False, compression="zstd")
    return path


def _row_hash(row: pd.Series) -> str:
    return hashlib.sha256(
        json.dumps(row.to_dict(), ensure_ascii=False, default=str, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _ymd(value: date) -> str:
    return value.strftime("%Y%m%d")
