import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

_log_fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s — %(message)s", datefmt="%H:%M:%S")

_console = logging.StreamHandler()
_console.setLevel(logging.INFO)
_console.setFormatter(_log_fmt)

_file = logging.FileHandler("../logs/agent.log", encoding="utf-8")
_file.setLevel(logging.DEBUG)
_file.setFormatter(_log_fmt)

logging.root.setLevel(logging.DEBUG)
logging.root.addHandler(_console)
logging.root.addHandler(_file)

from app.api.applications import router as applications_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.setup import router as setup_router
from app.api.workflows import router as workflows_router
from app.persistence.sqlite.applications import SqliteApplicationRepository, SqliteDraftRepository
from app.persistence.sqlite.connection import Database
from app.persistence.sqlite.job_analysis import SqliteJobAnalysisRepository
from app.persistence.sqlite.jobs import SqliteJobRepository
from app.persistence.sqlite.question_cache import SqliteQuestionCacheRepository
from app.persistence.sqlite.queue import SqliteQueueRepository
from app.persistence.sqlite.workflow_runs import SqliteWorkflowRunRepository, SqliteBrowserSessionRepository
from app.settings import get_settings
from app.tools.client import ToolClient
from app.worker.queue_worker import run_queue_worker


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = app.state.settings
    database = await Database.open(settings.resolved_sqlite_path)
    app.state.database = database
    app.state.job_repository = SqliteJobRepository(database.connection)
    app.state.job_analysis_repository = SqliteJobAnalysisRepository(database.connection)
    app.state.application_repository = SqliteApplicationRepository(database.connection)
    app.state.draft_repository = SqliteDraftRepository(database.connection)
    app.state.workflow_run_repository = SqliteWorkflowRunRepository(database.connection)
    app.state.browser_session_repository = SqliteBrowserSessionRepository(database.connection)
    app.state.queue_repository = SqliteQueueRepository(database.connection)
    app.state.question_cache_repository = SqliteQuestionCacheRepository(database.connection)
    app.state.tool_client = ToolClient(settings)

    worker_task = asyncio.create_task(run_queue_worker(app.state))
    try:
        yield
    finally:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        await app.state.tool_client.aclose()
        await database.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="job-agent",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.include_router(health_router, prefix="/api")
    app.include_router(jobs_router, prefix="/api")
    app.include_router(applications_router, prefix="/api")
    app.include_router(workflows_router, prefix="/api")
    app.include_router(setup_router)
    return app


app = create_app()
