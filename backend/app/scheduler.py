from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import Settings
from app.db.session import create_session_factory

logger = logging.getLogger(__name__)


class ResearchScheduler:
    """Optional research jobs isolated from formal selection and review."""

    def __init__(
        self,
        settings: Settings,
        jobs: dict[str, Callable[[], Any]] | None = None,
    ) -> None:
        self.settings = settings
        self.jobs = jobs or {}
        self.scheduler = AsyncIOScheduler(timezone=settings.timezone)

    def configure(self) -> None:
        definitions = (
            (
                "daily_factor_analysis",
                self.settings.daily_factor_analysis_enabled,
                CronTrigger(
                    day_of_week="mon-fri",
                    hour=18,
                    minute=45,
                    timezone=self.settings.timezone,
                ),
            ),
            (
                "weekly_parameter_research",
                self.settings.weekly_parameter_research_enabled,
                CronTrigger(
                    day_of_week="sat",
                    hour=9,
                    minute=0,
                    timezone=self.settings.timezone,
                ),
            ),
            (
                "weekly_strategy_validation",
                self.settings.weekly_strategy_validation_enabled,
                CronTrigger(
                    day_of_week="sat",
                    hour=13,
                    minute=0,
                    timezone=self.settings.timezone,
                ),
            ),
            (
                "monthly_qlib_experiment",
                self.settings.monthly_qlib_experiment_enabled,
                CronTrigger(
                    day=1,
                    hour=2,
                    minute=0,
                    timezone=self.settings.timezone,
                ),
            ),
        )
        for name, enabled, trigger in definitions:
            if not enabled:
                continue
            self.scheduler.add_job(
                self._isolated_job,
                trigger=trigger,
                id=name,
                name=name,
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                kwargs={"name": name},
                misfire_grace_time=3_600,
            )

    def start(self) -> None:
        self.configure()
        self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def _isolated_job(self, *, name: str) -> None:
        job = self.jobs.get(name)
        if job is None:
            logger.warning("scheduled job %s skipped: no task input configured", name)
            return
        try:
            job()
        except Exception:
            # Optional research failures must never stop daily formal workflows.
            logger.exception("optional scheduled job %s failed", name)


def build_scheduled_jobs(settings: Settings) -> dict[str, Callable[[], Any]]:
    """Build real jobs only for explicitly supplied JSON request configurations."""
    from app.api.schemas import (
        FactorAnalysisRunRequest,
        QlibExperimentRequest,
        RQAlphaValidationRequest,
        VectorBTResearchRequest,
    )
    from app.services.research_tasks import ResearchTaskService

    models: dict[str, tuple[Path | None, type[Any], str]] = {
        "daily_factor_analysis": (
            settings.daily_factor_analysis_config,
            FactorAnalysisRunRequest,
            "run_factor_analysis",
        ),
        "weekly_parameter_research": (
            settings.weekly_parameter_research_config,
            VectorBTResearchRequest,
            "run_vectorbt",
        ),
        "weekly_strategy_validation": (
            settings.weekly_strategy_validation_config,
            RQAlphaValidationRequest,
            "run_rqalpha",
        ),
        "monthly_qlib_experiment": (
            settings.monthly_qlib_experiment_config,
            QlibExperimentRequest,
            "run_qlib",
        ),
    }
    jobs: dict[str, Callable[[], Any]] = {}
    for name, (path, model_type, method_name) in models.items():
        if path is None:
            continue
        configured_path = path

        def execute(
            *,
            config_path: Path = configured_path,
            request_type: type[Any] = model_type,
            service_method: str = method_name,
            job_name: str = name,
        ) -> Any:
            payload = request_type.model_validate_json(config_path.read_text(encoding="utf-8"))
            if job_name == "monthly_qlib_experiment":
                payload.experiment_code = (
                    f"{payload.experiment_code}-{datetime.now().strftime('%Y%m')}"
                )
            factory = create_session_factory(settings.database_url)
            with factory() as session:
                service = ResearchTaskService(session, settings)
                return getattr(service, service_method)(payload)

        jobs[name] = execute
    return jobs
