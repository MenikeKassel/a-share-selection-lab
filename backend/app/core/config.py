from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ASHARE_",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "A-Share Selection Lab"
    environment: str = "development"
    api_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./data/a_share_selection_lab.db"
    data_root: Path = Path("./data")
    artifact_root: Path = Path("./data/artifacts")
    timezone: str = "Asia/Shanghai"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    scheduler_enabled: bool = False
    daily_factor_analysis_enabled: bool = True
    weekly_parameter_research_enabled: bool = False
    weekly_strategy_validation_enabled: bool = False
    monthly_qlib_experiment_enabled: bool = False
    daily_factor_analysis_config: Path | None = None
    weekly_parameter_research_config: Path | None = None
    weekly_strategy_validation_config: Path | None = None
    monthly_qlib_experiment_config: Path | None = None
    min_daily_coverage_ratio: float = 0.95
    min_minute_coverage_ratio: float = 0.0
    expected_universe_size: int = 0
    default_benchmark_symbol: str = "000300.SH"
    freestockdb_enabled: bool = True
    freestockdb_base_url: str = "http://127.0.0.1:7899"
    freestockdb_connect_timeout_seconds: float = 2.0
    freestockdb_read_timeout_seconds: float = 60.0
    freestockdb_max_concurrency: int = 8
    freestockdb_default_lookback_days: int = 400
    freestockdb_minute_lookback_days: int = 45

    @field_validator("data_root", "artifact_root", mode="after")
    @classmethod
    def normalize_path(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator(
        "daily_factor_analysis_config",
        "weekly_parameter_research_config",
        "weekly_strategy_validation_config",
        "monthly_qlib_experiment_config",
        mode="after",
    )
    @classmethod
    def normalize_optional_path(cls, value: Path | None) -> Path | None:
        return value.expanduser().resolve() if value is not None else None

    def ensure_directories(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.artifact_root.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
