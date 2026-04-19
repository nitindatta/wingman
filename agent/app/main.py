import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI

_ROOT_DIR = Path(__file__).resolve().parents[2]
_AGENT_LOG_PATH = _ROOT_DIR / "logs" / "agent.log"
_APP_LOGGERS = {
    "ai",
    "answer_field",
    "applications",
    "apply",
    "browser_client",
    "cover_letter",
    "jobs",
    "prepare",
    "queue_repo",
    "queue_worker",
}
_NOISY_LOGGERS = {
    "aiosqlite": logging.WARNING,
    "httpcore": logging.WARNING,
    "httpx": logging.INFO,
    "openai": logging.INFO,
    "multipart": logging.WARNING,
    "uvicorn.access": logging.INFO,
    "watchfiles": logging.WARNING,
}


def _is_first_party_logger(logger_name: str) -> bool:
    return logger_name == "app" or logger_name.startswith("app.") or logger_name in _APP_LOGGERS


class _AgentLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.WARNING or _is_first_party_logger(record.name)


def _configure_logging() -> None:
    root = logging.getLogger()
    if getattr(root, "_envoy_logging_configured", False):
        return

    log_fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(log_fmt)
    console.addFilter(_AgentLogFilter())

    _AGENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(_AGENT_LOG_PATH, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(log_fmt)
    file_handler.addFilter(_AgentLogFilter())

    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    root.addHandler(file_handler)

    for logger_name, level in _NOISY_LOGGERS.items():
        logging.getLogger(logger_name).setLevel(level)

    root._envoy_logging_configured = True  # type: ignore[attr-defined]


_configure_logging()

from app.api.applications import router as applications_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.profile_interview import router as profile_interview_router
from app.api.setup import router as setup_router
from app.api.workflows import router as workflows_router
from app.persistence.sqlite.applications import SqliteApplicationRepository, SqliteDraftRepository
from app.persistence.sqlite.connection import Database
from app.persistence.sqlite.job_analysis import SqliteJobAnalysisRepository
from app.persistence.sqlite.jobs import SqliteJobRepository
from app.persistence.sqlite.profile_interview import SqliteProfileInterviewRepository
from app.persistence.sqlite.profile_state import SqliteProfileStateRepository
from app.persistence.sqlite.question_cache import SqliteQuestionCacheRepository
from app.persistence.sqlite.queue import SqliteQueueRepository
from app.persistence.sqlite.workflow_runs import (
    SqliteBrowserSessionRepository,
    SqliteWorkflowRunRepository,
)
from app.providers import registry
from app.providers.indeed import IndeedAdapter
from app.providers.seek import SeekAdapter
from app.settings import get_settings
from app.tools.client import ToolClient
from app.worker.queue_worker import run_apply_worker, run_prepare_worker


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    registry.register("seek", SeekAdapter())
    registry.register("indeed", IndeedAdapter())

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
    app.state.profile_interview_repository = SqliteProfileInterviewRepository(database.connection)
    app.state.profile_state_repository = SqliteProfileStateRepository(database.connection)
    app.state.tool_client = ToolClient(settings)

    await app.state.queue_repository.reset_stale()
    prepare_task = asyncio.create_task(run_prepare_worker(app.state))
    apply_task = asyncio.create_task(run_apply_worker(app.state))
    try:
        yield
    finally:
        prepare_task.cancel()
        apply_task.cancel()
        for task in (prepare_task, apply_task):
            try:
                await task
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
    app.include_router(profile_interview_router)
    return app


app = create_app()
