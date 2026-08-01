"""All optional third-party integrations live below this package."""


class OptionalEngineUnavailableError(RuntimeError):
    """Raised when an explicitly requested optional engine is not installed."""
