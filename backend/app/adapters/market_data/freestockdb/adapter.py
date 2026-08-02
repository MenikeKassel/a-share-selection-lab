from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
import pandas as pd

from app.adapters.market_data.base import (
    DailyBarsRequest,
    MarketDataProviderStatus,
    MinuteBarsRequest,
)


class FreeStockDBError(RuntimeError):
    """A FreeStockDB response cannot be consumed safely."""


class FreeStockDBHttpAdapter:
    """Read-only HTTP adapter for the local FreeStockDB service.

    The service returns JSON without a charset header.  We decode bytes as
    UTF-8 explicitly instead of relying on a client's ISO-8859-1 fallback.
    """

    provider_code = "freestockdb"
    _stock_prefixes = (
        "000",
        "001",
        "002",
        "003",
        "300",
        "301",
        "302",
        "600",
        "601",
        "603",
        "605",
        "688",
        "689",
        "920",
    )

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:7899",
        connect_timeout_seconds: float = 2.0,
        read_timeout_seconds: float = 60.0,
        max_concurrency: int = 8,
        status_timeout_seconds: float = 5.0,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("FreeStockDB endpoint must be a local HTTP(S) address")
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        self.base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(
            timeout=read_timeout_seconds,
            connect=connect_timeout_seconds,
        )
        self._connect_timeout_seconds = connect_timeout_seconds
        self._status_timeout_seconds = status_timeout_seconds
        self.max_concurrency = max_concurrency

    def status(self) -> MarketDataProviderStatus:
        checked_at = datetime.now().astimezone().isoformat()
        try:
            daily = self._latest_daily_probe(requester=self._status_request_json)
            minute_keys = self._status_request_json(
                {
                    "cmd": "keys",
                    "t": "分钟k",
                    "k1": "all:",
                    "k2": f"key:{daily['date']}145200",
                }
            )
            minute_dates = _dates_from_keys(minute_keys, minute=True)
            return MarketDataProviderStatus(
                provider_code=self.provider_code,
                configured=True,
                reachable=True,
                endpoint=self.base_url,
                read_only=True,
                daily_latest_date=_to_date(daily.get("date")),
                minute_latest_date=max(minute_dates) if minute_dates else None,
                daily_instrument_count=int(daily.get("_instrument_count", 0)),
                minute_instrument_count=len(minute_keys) if isinstance(minute_keys, list) else 0,
                capabilities=(
                    "daily_ohlcv",
                    "minute_ohlcv",
                    "adjustment_factors",
                    "daily_valuation",
                    "daily_st_status",
                ),
                limitations=(
                    "historical_financial_available_at_not_provided",
                    "historical_industry_effective_dates_not_provided",
                    "csi300_index_series_not_provided",
                    "level2_microstructure_unavailable",
                ),
                checked_at=checked_at,
            )
        except Exception as error:  # provider health must never stop app startup
            return MarketDataProviderStatus(
                provider_code=self.provider_code,
                configured=True,
                reachable=False,
                endpoint=self.base_url,
                read_only=True,
                checked_at=checked_at,
                error=str(error),
            )

    def fetch_daily(self, request: DailyBarsRequest) -> pd.DataFrame:
        if request.start_date > request.end_date:
            raise ValueError("daily request start_date must not be after end_date")
        rows: list[dict[str, Any]] = []
        if request.symbols:
            with ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
                futures = {
                    pool.submit(self._fetch_symbol_daily, symbol, request): symbol
                    for symbol in request.symbols
                }
                for future in as_completed(futures):
                    rows.extend(future.result())
        else:
            # FreeStockDB can return a wildcard symbol range in one request.  A
            # request per symbol is prohibitively slow for a full-market snapshot;
            # split by the exchange/code prefix and a 31-day time window so each
            # response remains bounded even for the default 400-day snapshot.
            for prefix in self._stock_prefixes:
                window_start = request.start_date
                while window_start <= request.end_date:
                    window_end = min(window_start + timedelta(days=30), request.end_date)
                    rows.extend(
                        self._fetch_prefix_daily(
                            prefix, DailyBarsRequest(window_start, window_end)
                        )
                    )
                    window_start = window_end + timedelta(days=1)
        if not rows:
            return _empty_daily_frame()
        frame = pd.DataFrame(rows)
        return normalize_daily_frame(frame)

    def fetch_minute(self, request: MinuteBarsRequest) -> dict[str, pd.DataFrame]:
        symbols = tuple(request.symbols)
        if not symbols:
            return {}
        output: dict[str, pd.DataFrame] = {}
        with ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            futures = {
                pool.submit(self._fetch_symbol_minute, symbol, request): symbol
                for symbol in symbols
            }
            for future in as_completed(futures):
                symbol, rows = future.result()
                if rows:
                    output[symbol] = normalize_minute_frame(pd.DataFrame(rows), symbol=symbol)
        return output

    def fetch_daily_cross_section(
        self,
        trade_date: date,
        *,
        requester: Callable[[dict[str, str]], Any] | None = None,
    ) -> pd.DataFrame:
        request = requester or self._request_json
        rows = request(
            {
                "cmd": "vals",
                "t": "日k",
                "k1": "all:",
                "k2": f"key:{trade_date:%Y%m%d}",
            }
        )
        if not isinstance(rows, list):
            raise FreeStockDBError("daily cross-section response is not a list")
        return normalize_daily_frame(pd.DataFrame(rows))

    def fetch_adjustment_factors(self, symbols: Iterable[str]) -> pd.DataFrame:
        requested = {
            str(symbol).strip().zfill(6)
            for symbol in symbols
            if self.is_stock_symbol(str(symbol))
        }
        if not requested:
            return pd.DataFrame(
                columns=["symbol", "effective_date", "div", "give", "trans", "mult", "cum"]
            )
        if len(requested) > 100:
            prefix_rows: list[dict[str, Any]] = []
            for prefix in self._stock_prefixes:
                prefix_rows.extend(
                    row
                    for row in self._fetch_factor_prefix(prefix)
                    if str(row.get("symbol", "")).zfill(6) in requested
                )
            if prefix_rows:
                return pd.DataFrame(prefix_rows).sort_values(
                    ["symbol", "effective_date"]
                ).reset_index(drop=True)

        rows: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            futures = {
                pool.submit(self._fetch_symbol_factors, symbol): symbol for symbol in requested
            }
            for future in as_completed(futures):
                rows.extend(future.result())
        if not rows:
            return pd.DataFrame(
                columns=["symbol", "effective_date", "div", "give", "trans", "mult", "cum"]
            )
        return pd.DataFrame(rows).sort_values(["symbol", "effective_date"]).reset_index(drop=True)

    def _latest_daily_probe(
        self,
        *,
        requester: Callable[[dict[str, str]], Any] | None = None,
    ) -> dict[str, Any]:
        request = requester or self._request_json
        rows = request(
            {
                "cmd": "vals",
                "t": "日k",
                "k1": "key:000001",
                "k2": "all:",
                "num": "-1",
            }
        )
        if not isinstance(rows, list) or not rows:
            raise FreeStockDBError("no daily probe row returned")
        latest = dict(rows[0])
        try:
            cross = self.fetch_daily_cross_section(
                _to_date(latest["date"]), requester=request
            )
            latest["_instrument_count"] = len(cross)
        except Exception:
            latest["_instrument_count"] = 0
        return latest

    def _current_stock_symbols(self, as_of: date) -> list[str]:
        cross = self.fetch_daily_cross_section(as_of)
        if not cross.empty:
            return sorted(
                {
                    str(symbol).zfill(6)
                    for symbol in cross.loc[
                        cross["symbol"].astype(str).map(self.is_stock_symbol), "symbol"
                    ]
                }
            )
        raw = self._request_json({"cmd": "get", "t": "股票代码"})
        candidates: list[str] = []
        if isinstance(raw, dict):
            for values in raw.values():
                if isinstance(values, list):
                    candidates.extend(str(value).zfill(6) for value in values)
        return sorted({symbol for symbol in candidates if self.is_stock_symbol(symbol)})

    @classmethod
    def is_stock_symbol(cls, symbol: str) -> bool:
        value = str(symbol).strip().zfill(6)
        return len(value) == 6 and value.startswith(cls._stock_prefixes)

    def _fetch_symbol_daily(self, symbol: str, request: DailyBarsRequest) -> list[dict[str, Any]]:
        rows = self._request_json(
            {
                "cmd": "vals",
                "t": "日k",
                "k1": f"key:{symbol}",
                "k2": f"fwd:{request.start_date:%Y%m%d},{request.end_date:%Y%m%d}",
            }
        )
        return (
            [dict(row) for row in rows if isinstance(row, dict)]
            if isinstance(rows, list)
            else []
        )

    def _fetch_prefix_daily(self, prefix: str, request: DailyBarsRequest) -> list[dict[str, Any]]:
        rows = self._request_json(
            {
                "cmd": "vals",
                "t": "日k",
                "k1": f"key:{prefix}*",
                "k2": f"fwd:{request.start_date:%Y%m%d},{request.end_date:%Y%m%d}",
            }
        )
        return (
            [dict(row) for row in rows if isinstance(row, dict)]
            if isinstance(rows, list)
            else []
        )

    def _fetch_factor_prefix(self, prefix: str) -> list[dict[str, Any]]:
        raw = self._request_json(
            {"cmd": "get", "t": "复权", "k1": f"key:{prefix}*", "k2": "all:"}
        )
        rows: list[dict[str, Any]] = []
        if not isinstance(raw, list):
            return rows
        for item in raw:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            key, value = item[0], item[1]
            if not isinstance(value, dict):
                continue
            match = re.search(r"复权:(\d{6}):(\d{8})$", str(key))
            if not match:
                continue
            rows.append(
                {
                    "symbol": match.group(1),
                    "effective_date": pd.to_datetime(
                        match.group(2), format="%Y%m%d"
                    ).date(),
                    **{name: value.get(name) for name in ("div", "give", "trans", "mult", "cum")},
                }
            )
        return rows

    def _fetch_symbol_factors(self, symbol: str) -> list[dict[str, Any]]:
        keys = self._request_json(
            {"cmd": "keys", "t": "复权", "k1": f"key:{symbol}", "k2": "all:"}
        )
        values = self._request_json(
            {"cmd": "vals", "t": "复权", "k1": f"key:{symbol}", "k2": "all:"}
        )
        if not isinstance(keys, list) or not isinstance(values, list):
            return []
        rows: list[dict[str, Any]] = []
        for key, value in zip(keys, values, strict=False):
            match = re.search(r":(\d{8})$", str(key))
            if not match or not isinstance(value, dict):
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "effective_date": pd.to_datetime(match.group(1), format="%Y%m%d").date(),
                    **{name: value.get(name) for name in ("div", "give", "trans", "mult", "cum")},
                }
            )
        return rows

    def _fetch_symbol_minute(
        self, symbol: str, request: MinuteBarsRequest
    ) -> tuple[str, list[dict[str, Any]]]:
        rows = self._request_json(
            {
                "cmd": "vals",
                "t": "分钟k",
                "k1": f"key:{symbol}",
                "k2": f"fwd:{request.start_timestamp},{request.end_timestamp}",
            }
        )
        clean = (
            [dict(row) for row in rows if isinstance(row, dict)]
            if isinstance(rows, list)
            else []
        )
        return symbol, clean

    def _request_json(self, params: dict[str, str]) -> Any:
        return self._request_json_with_timeout(params)

    def _status_request_json(self, params: dict[str, str]) -> Any:
        return self._request_json_with_timeout(
            params, timeout_seconds=self._status_timeout_seconds
        )

    def _request_json_with_timeout(
        self,
        params: dict[str, str],
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        try:
            # The provider is explicitly local; do not route it through a
            # machine-wide HTTP proxy or leak request metadata outside the host.
            timeout = self._timeout
            if timeout_seconds is not None:
                timeout = httpx.Timeout(
                    timeout=timeout_seconds,
                    connect=min(self._connect_timeout_seconds, timeout_seconds),
                )
            with httpx.Client(timeout=timeout, trust_env=False) as client:
                response = client.get(self.base_url + "/", params=params)
                response.raise_for_status()
                return json.loads(response.content.decode("utf-8"))
        except (httpx.HTTPError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FreeStockDBError(f"FreeStockDB request failed: {error}") from error


def normalize_daily_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Convert the provider payload into the project's daily contract."""

    if frame.empty:
        return _empty_daily_frame()
    output = frame.copy()
    output["date"] = pd.to_datetime(output["date"].astype(str), format="%Y%m%d", errors="coerce")
    output["symbol"] = output["code"].astype(str).str.zfill(6)
    for column in (
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "volume",
        "amount",
        "turnover",
        "total_mv",
        "float_mv",
        "pe_ttm",
        "pb",
    ):
        if column in output:
            output[column] = pd.to_numeric(output[column], errors="coerce")
    output["source_pre_close"] = (
        output["pre_close"] if "pre_close" in output else pd.Series(pd.NA, index=output.index)
    )
    output["volume_unit"] = "shares"
    output["source"] = "freestockdb"
    volume = output["volume"] if "volume" in output else pd.Series(0, index=output.index)
    output["suspended"] = volume.fillna(0).eq(0)
    output["limit_up"] = False
    output["limit_down"] = False
    output["one_word_limit_up"] = False
    output["one_word_limit_down"] = False
    output = output.loc[
        output["date"].notna()
        & output["symbol"].map(FreeStockDBHttpAdapter.is_stock_symbol)
    ]
    output = output.sort_values(["symbol", "date"]).drop_duplicates(["symbol", "date"])
    output["pre_close"] = (
        output.groupby("symbol")["close"].shift(1).fillna(output["source_pre_close"])
    )
    return output.reset_index(drop=True)


def normalize_minute_frame(frame: pd.DataFrame, *, symbol: str | None = None) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "timestamp",
                "symbol",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
            ]
        )
    output = frame.copy()
    output["timestamp"] = pd.to_datetime(
        output["date"].astype(str), format="%Y%m%d%H%M%S", errors="coerce"
    )
    if "code" in output:
        output["symbol"] = output["code"].astype(str).str.zfill(6)
    else:
        output["symbol"] = str(symbol or "")
    for column in ("open", "high", "low", "close", "volume", "amount"):
        output[column] = pd.to_numeric(output[column], errors="coerce")
    output["volume_unit"] = "shares"
    output["source"] = "freestockdb"
    return (
        output.loc[output["timestamp"].notna()]
        .sort_values("timestamp")
        .drop_duplicates(["symbol", "timestamp"])
        .reset_index(drop=True)
    )


def apply_adjustment_factors(daily: pd.DataFrame, factors: pd.DataFrame) -> pd.DataFrame:
    """Attach source factors and reproduce FreeStockDB's documented formulas."""

    if daily.empty or factors.empty:
        output = daily.copy()
        output["adj_factor"] = 1.0
        return output
    output = daily.sort_values(["symbol", "date"]).copy()
    source = factors.sort_values(["symbol", "effective_date"]).copy()
    output["date"] = pd.to_datetime(output["date"], errors="coerce")
    source["effective_date"] = pd.to_datetime(
        source["effective_date"], errors="coerce"
    )
    source = source.loc[source["effective_date"].notna()]
    output = pd.merge_asof(
        output,
        source,
        left_on="date",
        right_on="effective_date",
        by="symbol",
        direction="backward",
    )
    output["adj_factor"] = pd.to_numeric(output["cum"], errors="coerce").fillna(1.0)
    latest = output.groupby("symbol")["adj_factor"].transform("max").replace(0, 1.0)
    ratio = latest / output["adj_factor"].replace(0, 1.0)
    for column in ("open", "high", "low", "close"):
        output[f"qfq_{column}"] = (output[column] / ratio).round(4)
        output[f"hfq_{column}"] = (output[column] / output["adj_factor"].replace(0, 1.0)).round(4)
    output["qfq_pre_close"] = output.groupby("symbol")["qfq_close"].shift(1)
    return output


def _empty_daily_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "date",
            "symbol",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
        ]
    )


def _dates_from_keys(keys: Any, *, minute: bool) -> list[date]:
    if not isinstance(keys, list):
        return []
    pattern = r":(\d{8})\d{6}$" if minute else r":(\d{8})$"
    result: list[date] = []
    for key in keys:
        match = re.search(pattern, str(key))
        if match:
            result.append(pd.to_datetime(match.group(1), format="%Y%m%d").date())
    return result


def _to_date(value: Any) -> date:
    parsed = pd.to_datetime(str(value), format="%Y%m%d", errors="raise")
    return parsed.date()
