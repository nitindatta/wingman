"""Background queue worker — processes work_queue items serially."""

from __future__ import annotations

import asyncio
import contextlib
import json as _json
import logging
from typing import Any

log = logging.getLogger("queue_worker")


async def run_queue_worker(app_state: Any) -> None:
    """Runs forever as an asyncio task. Call from FastAPI lifespan."""
    log.info("[worker] started")
    while True:
        try:
            queue_repo = app_state.queue_repository
            item = await queue_repo.claim_next()
            if item is None:
                await asyncio.sleep(1.5)
                continue

            log.info(
                "[worker] claimed item id=%s type=%s entity=%s",
                item.id, item.queue_type, item.entity_id,
            )

            try:
                if item.queue_type == "prepare":
                    await _handle_prepare(item, app_state)
                elif item.queue_type in ("apply", "resume"):
                    await _handle_apply_or_resume(item, app_state)
                else:
                    log.warning("[worker] unknown queue_type=%s", item.queue_type)
                await queue_repo.mark_done(item.id)
                log.info("[worker] done item id=%s", item.id)
            except Exception as exc:
                log.exception("[worker] item %s failed: %s", item.id, exc)
                await queue_repo.mark_failed(item.id, str(exc))
                # Mark application as failed if we can
                with contextlib.suppress(Exception):
                    await app_state.application_repository.update_state(item.entity_id, "failed")

        except asyncio.CancelledError:
            log.info("[worker] cancelled — shutting down")
            raise
        except Exception as outer_exc:
            log.exception("[worker] outer loop error: %s", outer_exc)
            await asyncio.sleep(5)


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
