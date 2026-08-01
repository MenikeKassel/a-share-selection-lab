from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routes import (
    backtests,
    comparisons,
    engines,
    experiments,
    factor_analysis,
    selections,
    walk_forward,
)
from app.core.config import get_settings
from app.db.session import initialize_database
from app.scheduler import ResearchScheduler, build_scheduled_jobs


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.ensure_directories()
    initialize_database()
    scheduler = ResearchScheduler(settings, jobs=build_scheduled_jobs(settings))
    if settings.scheduler_enabled:
        scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=("A股每日选股、透明因子研究与自动复盘。不自动下单，不承诺或预测“明天必涨停”。"),
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    prefix = settings.api_prefix
    application.include_router(engines.router, prefix=prefix)
    application.include_router(factor_analysis.router, prefix=prefix)
    application.include_router(backtests.router, prefix=prefix)
    application.include_router(experiments.router, prefix=prefix)
    application.include_router(comparisons.router, prefix=prefix)
    application.include_router(selections.router, prefix=prefix)
    application.include_router(walk_forward.router, prefix=prefix)

    @application.get(f"{prefix}/health", tags=["system"])
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "version": __version__,
            "automatic_ordering": False,
            "guaranteed_limit_up_prediction": False,
        }

    return application


app = create_app()
