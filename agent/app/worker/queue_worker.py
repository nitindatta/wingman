"""Background queue workers — two lanes:

  prepare-lane  — pool of N concurrent tasks (LLM-only, no browser)
  apply-lane    — single serial task (browser, Chrome profile lock)

Concurrency for the prepare lane is set by settings.worker_prepare_concurrency
(default 2, env var WORKER_PREPARE_CONCURRENCY).
"""

from __future__ import annotations

import asyncio
import contextlib
import json as _json
import logging
from typing import Any

log = logging.getLogger("queue_worker")

# ── Prepare lane ───────────────────────────────────────────────────────────────

async def run_prepare_worker(app_state: Any) -> None:
    """Pool-based worker for 'prepare' items.

    Spawns up to `settings.worker_prepare_concurrency` concurrent prepare tasks.
    Each slot is released as soon as the task finishes so the next item can start.
    """
    concurrency = app_state.settings.worker_prepare_concurrency
    semaphore = asyncio.Semaphore(concurrency)
    log.info("[prepare-worker] started concurrency=%d", concurrency)

    while True:
        try:
            # Only try to claim when a slot is free
            await semaphore.acquire()

            item = await app_state.queue_repository.claim_next_of_types(["prepare"])
            if item is None:
                semaphore.release()
                await asyncio.sleep(1.5)
                continue

            log.info(
                "[prepare-worker] claimed id=%s entity=%s",
                item.id, item.entity_id,
            )

            async def _run(i=item):
                try:
                    await _handle_prepare(i, app_state)
                    await app_state.queue_repository.mark_done(i.id)
                    log.info("[prepare-worker] done id=%s", i.id)
                except Exception as exc:
                    log.exception("[prepare-worker] item %s failed: %s", i.id, exc)
                    await app_state.queue_repository.mark_failed(i.id, str(exc))
                    with contextlib.suppress(Exception):
                        await app_state.application_repository.update_state(i.entity_id, "failed")
                finally:
                    semaphore.release()

            asyncio.create_task(_run())

        except asyncio.CancelledError:
            log.info("[prepare-worker] cancelled — shutting down")
            raise
        except Exception as outer_exc:
            log.exception("[prepare-worker] outer loop error: %s", outer_exc)
            semaphore.release()
            await asyncio.sleep(5)


# ── Apply lane ─────────────────────────────────────────────────────────────────

async def run_apply_worker(app_state: Any) -> None:
    """Serial worker for 'apply' and 'resume' items.

    Must stay serial — Playwright uses a single Chrome profile directory which
    Chrome locks exclusively. Two concurrent sessions against the same profile
    will crash.
    """
    log.info("[apply-worker] started (serial)")

    while True:
        try:
            item = await app_state.queue_repository.claim_next_of_types(["apply", "resume"])
            if item is None:
                await asyncio.sleep(1.5)
                continue

            log.info(
                "[apply-worker] claimed id=%s type=%s entity=%s",
                item.id, item.queue_type, item.entity_id,
            )

            try:
                await _handle_apply_or_resume(item, app_state)
                await app_state.queue_repository.mark_done(item.id)
                log.info("[apply-worker] done id=%s", item.id)
            except Exception as exc:
                log.exception("[apply-worker] item %s failed: %s", item.id, exc)
                await app_state.queue_repository.mark_failed(item.id, str(exc))
                with contextlib.suppress(Exception):
                    await app_state.application_repository.update_state(item.entity_id, "failed")

        except asyncio.CancelledError:
            log.info("[apply-worker] cancelled — shutting down")
            raise
        except Exception as outer_exc:
            log.exception("[apply-worker] outer loop error: %s", outer_exc)
            await asyncio.sleep(5)


# ── Handlers (unchanged) ───────────────────────────────────────────────────────

async def _handle_prepare(item: Any, app_state: Any) -> None:
    from app.workflows.prepare import run_prepare

    app_id = item.entity_id
    app = await app_state.application_repository.get(app_id)
    if app is None:
        raise ValueError(f"application {app_id} not found")

    state = await run_prepare(
        app_state.settings,
        app_state.tool_client,
        app_state.job_repository,
        app_state.application_repository,
        app_state.draft_repository,
        app_state.job_analysis_repository,
        job_id=app.job_id,
        existing_app_id=app_id,
    )

    if state.error:
        await app_state.application_repository.update_state(app_id, "failed")
    # is_suitable + state already updated inside run_prepare's persist node


async def _handle_apply_or_resume(item: Any, app_state: Any) -> None:
    from app.api.workflows import _apply_response
    from app.workflows.apply import resume_apply, run_apply

    app_id = item.entity_id
    app_repo = app_state.application_repository

    if item.queue_type == "apply":
        run_repo = app_state.workflow_run_repository
        run_id = await run_repo.create(application_id=app_id, workflow_type="apply")
        apply_state = await run_apply(
            app_state.settings,
            app_state.tool_client,
            app_repo,
            app_state.draft_repository,
            run_repo,
            app_state.browser_session_repository,
            app_state.database.connection,
            application_id=app_id,
            workflow_run_id=run_id,
            question_cache=app_state.question_cache_repository,
        )
    else:  # resume
        payload = item.payload
        apply_state = await resume_apply(
            app_state.settings,
            app_state.tool_client,
            app_repo,
            app_state.draft_repository,
            app_state.workflow_run_repository,
            app_state.browser_session_repository,
            app_state.database.connection,
            workflow_run_id=payload["run_id"],
            approved_values=payload.get("approved_values", {}),
            action_label=payload.get("action_label", "Continue"),
            action=payload.get("action", "continue"),
            question_cache=app_state.question_cache_repository,
        )

    # Build response and persist it so portal can poll
    response = _apply_response(apply_state)
    await app_repo.update_apply_step(app_id, _json.dumps(response.model_dump()))

    # Update application state for interrupt cases (node_finish didn't run yet)
    status = response.status
    if status == "needs_review":
        await app_repo.update_state(app_id, "needs_review")
    elif status == "awaiting_submit":
        await app_repo.update_state(app_id, "awaiting_submit")
    # Terminal states (applied/failed/paused) already updated by node_finish
