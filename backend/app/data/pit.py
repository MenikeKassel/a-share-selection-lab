"""Point-in-time cutoff semantics (PR 5).

Single implementation of the after-close information boundary:

    available_at <= signal_date 18:30 Asia/Shanghai  -> usable for signal_date
    available_at >  signal_date 18:30                -> NOT usable until later
    date-only records (no time part)                 -> deferred to the next
                                                       trading day's cutoff
    weekends / holidays                              -> advanced by the
                                                       trading calendar

Every as-of join (financials, valuations, industry, ST state, security
state) must route through :func:`asof_for_signal_date` so the cutoff rule
cannot drift between modules.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

import pandas as pd

AFTER_CLOSE_CUTOFF = time(18, 30)
SHANGHAI = "Asia/Shanghai"

# Records whose available_at has a zero time part AND whose original text
# had no time component are date-only; they defer to the next trading day.
_DATE_ONLY_HOUR = 0


def _parse_available(frame: pd.DataFrame) -> pd.Series:
    available = pd.to_datetime(frame["available_at"], format="mixed", errors="coerce")
    if available.isna().any():
        raise ValueError("available_at contains invalid timestamps")
    return pd.Series(available, index=frame.index, dtype="datetime64[ns]")


def _date_only_mask(frame: pd.DataFrame, available: pd.Series) -> pd.Series:
    """True when the record was published as a date without a time part."""
    raw = frame["available_at"].astype(str).str.strip()
    has_time = raw.str.contains(r"[:T]", regex=True).fillna(False).astype(bool)
    mask = (~has_time) & (available.dt.hour == _DATE_ONLY_HOUR)
    return pd.Series(mask, index=frame.index, dtype=bool)


def _cutoff_for(day: pd.Timestamp, cutoff: time) -> pd.Timestamp:
    """The after-close cutoff instant for a trading day (Shanghai time)."""
    base = day.normalize()
    if base.tz is not None:
        base = base.tz_localize(None)
    return pd.Timestamp(
        datetime(base.year, base.month, base.day, cutoff.hour, cutoff.minute, cutoff.second),
        tz=SHANGHAI,
    )


def next_trading_day_after(
    day: pd.Timestamp,
    trading_calendar: pd.DataFrame,
    *,
    calendar_date_column: str = "date",
) -> pd.Timestamp:
    """Smallest trading day strictly after ``day`` from the calendar."""
    calendar_dates = pd.to_datetime(
        trading_calendar[calendar_date_column], format="mixed", errors="coerce"
    ).dt.normalize()
    calendar_unique: pd.Series = calendar_dates.dropna().unique()  # type: ignore[assignment]
    calendar_index = pd.DatetimeIndex(sorted(calendar_unique))
    future = calendar_index[calendar_index > day.normalize()]
    if len(future) == 0:
        raise ValueError(f"trading calendar has no day after {day.date()}")
    return pd.Timestamp(future[0])


def asof_for_signal_date(
    frame: pd.DataFrame,
    signal_date: Any,
    *,
    cutoff: time = AFTER_CLOSE_CUTOFF,
    trading_calendar: pd.DataFrame | None = None,
    strict: bool = False,
) -> pd.DataFrame:
    """Filter ``frame`` to records known by the signal date's 18:30 cutoff.

    Rules
    -----
    * timestamped ``available_at <= signal_date 18:30`` -> kept.
    * timestamped ``available_at >  signal_date 18:30`` -> dropped.
    * date-only records (no time part) -> treated as released at 18:30 on
      their date, so they are usable only for signal dates strictly after
      their own date (next trading day or later).
    * ``strict=True`` with date-only records -> ValueError (no
      approximation allowed).
    * ``available_at < published_at`` -> ValueError (audit failure).
    """
    if frame is None or frame.empty:
        return frame
    required = {"available_at", "published_at"}
    if missing := required.difference(frame.columns):
        raise ValueError(f"frame is missing PIT columns: {sorted(missing)}")

    available = _parse_available(frame)
    published = pd.to_datetime(frame["published_at"], format="mixed", errors="coerce")
    if published.isna().any():
        raise ValueError("published_at contains invalid timestamps")
    if (available < published).any():
        raise ValueError("available_at earlier than published_at (audit failure)")

    if available.dt.tz is None:
        available = available.dt.tz_localize(SHANGHAI)
    else:
        available = available.dt.tz_convert(SHANGHAI)

    signal = pd.Timestamp(signal_date).normalize()
    if signal.tz is None:
        signal = signal.tz_localize(SHANGHAI)
    cutoff_instant = _cutoff_for(signal, cutoff)

    date_only = _date_only_mask(frame, available)
    if strict and date_only.any():
        raise ValueError(
            "strict PIT mode: date-only records (no time) are not allowed; "
            "defer them to the next trading day explicitly"
        )

    # Date-only records are known only after their own date's close, so a
    # signal on the same date cannot see them; any later signal date can.
    date_only_usable = date_only & (available.dt.normalize() < signal)
    timestamped_usable = (~date_only) & (available <= cutoff_instant)
    mask = date_only_usable | timestamped_usable
    return pd.DataFrame(frame.loc[mask])
