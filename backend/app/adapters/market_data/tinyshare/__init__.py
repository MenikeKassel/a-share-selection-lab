"""Isolated TinyShare bridge.

The application never imports the third-party package.  It invokes a short-
lived worker interpreter through :class:`TinyShareIsolatedClient` and consumes
only JSON records returned by that worker.
"""

from app.adapters.market_data.tinyshare.client import (
    TinyShareCapability,
    TinyShareIsolatedClient,
    TinyShareProviderError,
)

__all__ = ["TinyShareCapability", "TinyShareIsolatedClient", "TinyShareProviderError"]
