"""Corporate-action execution modes and event contract (PR 1).

Three modes are defined:

- ``explicit``: per-event detail (cash dividends, bonus shares,
  capitalization, splits, rights issues) drives positions and cash.
- ``total_return_proxy``: no event detail; economic return comes from the
  causal adjusted (total-return) price series, raw prices are used only for
  tradability, initial board lots, and fees.  No position quantity is ever
  changed from ``adj_factor`` and no cash dividend is paid (would double
  count the total-return series).
- ``unavailable``: research only; execution is blocked.

The proxy contract is the fallback chosen for v8 after the TinyShare probe
failed (license expired), per the approved decision D1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

# action_type values in an explicit event table
ACTION_CASH_DIVIDEND = "cash_dividend"
ACTION_BONUS_SHARE = "bonus_share"
ACTION_CAPITALIZATION = "capitalization"
ACTION_SPLIT = "split"
ACTION_RIGHTS_ISSUE = "rights_issue"

CORPORATE_ACTION_MODES = ("explicit", "total_return_proxy", "unavailable")


@dataclass(frozen=True, slots=True)
class CorporateActionEvent:
    """One explicit corporate-action event (strict mode input)."""

    symbol: str
    ex_date: date
    action_type: str
    cash_dividend_per_share: float = 0.0
    bonus_share_ratio: float = 0.0
    capitalization_ratio: float = 0.0
    split_ratio: float = 1.0
    rights_issue_ratio: float = 0.0
    rights_issue_price: float = 0.0
    source: str = ""

    @property
    def share_multiplier(self) -> float:
        """Quantity multiplier for bonus + capitalization + split.

        Rights issues are NOT included: they require an explicit policy and
        cash, so they are handled separately (default ignore).
        """
        return (1.0 + self.bonus_share_ratio + self.capitalization_ratio) * self.split_ratio


@dataclass(slots=True)
class CorporateActionCapabilities:
    """What the snapshot can support, surfaced in the manifest and reports."""

    explicit_events_available: bool = False
    total_return_proxy_available: bool = True
    coverage_start: date | None = None
    coverage_end: date | None = None
    source: str = "causal_adj_factor"

    @property
    def strict_execution_eligible(self) -> bool:
        return self.explicit_events_available

    def as_dict(self) -> dict[str, Any]:
        return {
            "explicit_events_available": self.explicit_events_available,
            "total_return_proxy_available": self.total_return_proxy_available,
            "coverage_start": self.coverage_start.isoformat() if self.coverage_start else None,
            "coverage_end": self.coverage_end.isoformat() if self.coverage_end else None,
            "source": self.source,
            "strict_execution_eligible": self.strict_execution_eligible,
        }

    @classmethod
    def from_manifest(cls, manifest: dict[str, Any] | None) -> CorporateActionCapabilities:
        """Build from a snapshot manifest's corporate_actions block."""
        if not manifest:
            return cls()
        block = manifest.get("corporate_actions") or {}
        if not isinstance(block, dict):
            return cls()
        coverage_start = block.get("coverage_start")
        coverage_end = block.get("coverage_end")
        return cls(
            explicit_events_available=bool(block.get("explicit_events_available", False)),
            total_return_proxy_available=bool(
                block.get("total_return_proxy_available", True)
            ),
            coverage_start=(
                date.fromisoformat(coverage_start) if coverage_start else None
            ),
            coverage_end=date.fromisoformat(coverage_end) if coverage_end else None,
            source=str(block.get("source", "causal_adj_factor")),
        )


def resolve_execution_mode(
    capabilities: CorporateActionCapabilities,
    requested_mode: str = "total_return_proxy",
) -> str:
    """Pick the effective corporate-action mode.

    - requested ``explicit`` requires explicit events, else ``unavailable``
      (blocked) when strict execution is requested without data.
    - requested ``total_return_proxy`` is available as long as the proxy is.
    """
    if requested_mode == "explicit":
        return "explicit" if capabilities.explicit_events_available else "unavailable"
    if requested_mode == "total_return_proxy":
        return "total_return_proxy" if capabilities.total_return_proxy_available else "unavailable"
    return "unavailable"


#: Lot-level proxy record attached to every buy in proxy mode.
@dataclass(slots=True)
class ProxyLotRecord:
    entry_raw_notional: float = 0.0
    entry_adj_open: float = 0.0
    entry_raw_quantity: int = 0
    entry_adj_close: float = 0.0
    lot_id: str = field(default="")


def proxy_market_value(
    lot: ProxyLotRecord,
    causal_adj_close: float,
) -> float:
    """Total-return proxy mark of one lot at time t.

    proxy_value(t) = entry_raw_notional * adj_close(t) / adj_open(entry)
    """
    if lot.entry_adj_open <= 0:
        return 0.0
    return lot.entry_raw_notional * causal_adj_close / lot.entry_adj_open


def proxy_exit_value(
    lot: ProxyLotRecord,
    causal_adj_open_at_exit: float,
) -> float:
    """Proceeds proxy when selling the lot at next open."""
    if lot.entry_adj_open <= 0:
        return 0.0
    return lot.entry_raw_notional * causal_adj_open_at_exit / lot.entry_adj_open
