"""Application and draft API routes."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.persistence.sqlite.applications import SqliteApplicationRepository, SqliteDraftRepository

log = logging.getLogger("applications")

router = APIRouter()


@router.get("/applications", response_model=dict)
async def list_applications(request: Request, limit: int = 50, state: str | None = None):
    app_repo: SqliteApplicationRepository = request.app.state.application_repository
    job_repo = request.app.state.job_repository

    # Default: exclude discarded
    apps = await app_repo.list_all(limit=limit, state=state, exclude_discarded=True)

    results = []
    for a in apps:
        job = await job_repo.get(a.job_id)
        results.append({
            **a.model_dump(),
            "job_title": job.title if job else None,
            "job_company": job.company if job else None,
            "job_location": job.location if job else None,
            "job_source_url": job.source_url if job else None,
            "job_summary": job.summary if job else None,
            "job_payload": job.payload if job else {},
        })
    return {"applications": results}


@router.get("/applications/{app_id}", response_model=dict)
async def get_application(app_id: str, request: Request):
    app_repo: SqliteApplicationRepository = request.app.state.application_repository
    draft_repo: SqliteDraftRepository = request.app.state.draft_repository
    job_repo = request.app.state.job_repository

    app = await app_repo.get(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")

    job = await job_repo.get(app.job_id)
    drafts = await draft_repo.list_for_application(app_id)

    # Get cover letter and match evidence from drafts
    cover_letter = next((d.content for d in drafts if d.draft_type == "cover_letter"), "")
    match_evidence = next((d.content for d in drafts if d.draft_type == "match_evidence"), "")

    return {
        "application": app.model_dump(),
        "drafts": [d.model_dump() for d in drafts],
        "cover_letter": cover_letter,
        "match_evidence": match_evidence,
        "last_apply_step": app.last_apply_step_json,  # raw JSON string or None
        "job": {
            "title": job.title if job else None,
            "company": job.company if job else None,
            "location": job.location if job else None,
            "source_url": job.source_url if job else None,
            "summary": job.summary if job else None,
            "payload": job.payload if job else {},
        } if job else None,
    }


class ApproveRequest(BaseModel):
    cover_letter: str | None = None


@router.post("/applications/{app_id}/approve", response_model=dict)
async def approve_application(app_id: str, request: Request, body: ApproveRequest | None = None):
    if body is None:
        body = ApproveRequest()
    repo: SqliteApplicationRepository = request.app.state.application_repository
    draft_repo: SqliteDraftRepository = request.app.state.draft_repository
    app = await repo.get(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")
    approvable_states = {"prepared", "approved", "paused", "failed"}
    if app.state not in approvable_states:
        raise HTTPException(status_code=409, detail=f"cannot approve from state '{app.state}'")

    # Persist edited cover letter if provided
    if body.cover_letter is not None:
        drafts = await draft_repo.list_for_application(app_id)
        cl_draft = next((d for d in drafts if d.draft_type == "cover_letter"), None)
        if cl_draft:
            await draft_repo.update_content(cl_draft.id, body.cover_letter)

    await repo.update_state(app_id, "approved")
    return {"application_id": app_id, "state": "approved"}


@router.post("/applications/{app_id}/discard", response_model=dict)
async def discard_application(app_id: str, request: Request):
    repo: SqliteApplicationRepository = request.app.state.application_repository
    app = await repo.get(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")
    await repo.update_state(app_id, "discarded")
    return {"application_id": app_id, "state": "discarded"}


@router.post("/applications/{app_id}/cancel", response_model=dict)
async def cancel_application(app_id: str, request: Request):
    """Cancel an in-progress application (preparing / applying / submitting).

    Marks the application as discarded and kills any pending/processing queue
    items so the worker won't pick them up. The job is moved back to 'discovered'
    so the user can re-queue it later if they wish.
    """
    repo: SqliteApplicationRepository = request.app.state.application_repository
    queue_repo = request.app.state.queue_repository
    job_repo = request.app.state.job_repository

    app = await repo.get(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")

    cancellable_states = {"preparing", "applying", "submitting", "prepared", "approved", "needs_review", "awaiting_submit"}
    if app.state not in cancellable_states:
        raise HTTPException(status_code=409, detail=f"cannot cancel from state '{app.state}'")

    # Kill any queued work for this application
    cancelled = await queue_repo.cancel_for_entity(app_id)
    log.info("[cancel] app_id=%s queue_items_cancelled=%d", app_id, cancelled)

    # Move application to discarded
    await repo.update_state(app_id, "discarded")

    # Move job back to discovered so user can re-queue later
    await job_repo.update_state(app.job_id, "discovered")

    return {"application_id": app_id, "state": "discarded", "queue_items_cancelled": cancelled}


@router.post("/applications/{app_id}/reset", response_model=dict)
async def reset_application(app_id: str, request: Request):
    """Reset apply progress for testing while preserving prepared artifacts."""
    repo: SqliteApplicationRepository = request.app.state.application_repository
    queue_repo = request.app.state.queue_repository

    app = await repo.get(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")

    resettable_states = {
        "prepared",
        "approved",
        "applying",
        "needs_review",
        "awaiting_submit",
        "submitting",
        "paused",
        "failed",
        "applied",
    }
    if app.state not in resettable_states:
        raise HTTPException(status_code=409, detail=f"cannot reset from state '{app.state}'")

    cancelled = await queue_repo.cancel_for_entity(app_id)
    await repo.reset_apply_progress(app_id, target_state="approved")
    log.info("[reset] app_id=%s queue_items_cancelled=%d", app_id, cancelled)
    return {"application_id": app_id, "state": "approved", "queue_items_cancelled": cancelled}


@router.post("/applications/{app_id}/mark_submitted", response_model=dict)
async def mark_submitted(app_id: str, request: Request):
    """Mark an application as submitted — used when the portal redirected to an external ATS."""
    repo: SqliteApplicationRepository = request.app.state.application_repository
    job_repo = request.app.state.job_repository
    app = await repo.get(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")
    await repo.update_state(app_id, "applied")
    # Move the job out of review so it doesn't show on the Review Desk
    await job_repo.update_state(app.job_id, "ignored")
    return {"application_id": app_id, "state": "applied"}


# ── Async queue endpoints ──────────────────────────────────────────────────────

@router.post("/applications/{app_id}/apply", response_model=dict)
async def enqueue_apply(app_id: str, request: Request):
    """Enqueue an apply workflow run for an approved application."""
    repo: SqliteApplicationRepository = request.app.state.application_repository
    queue_repo = request.app.state.queue_repository

    app = await repo.get(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")

    applyable_states = {"approved", "paused", "failed"}
    if app.state not in applyable_states:
        raise HTTPException(status_code=409, detail=f"cannot apply from state '{app.state}'")

    await repo.update_state(app_id, "applying")
    await queue_repo.enqueue("apply", app_id)
    return {"application_id": app_id, "state": "applying"}


class GateResumeRequest(BaseModel):
    run_id: str
    approved_values: dict[str, str]


class ExternalHarnessRequest(BaseModel):
    target_url: str | None = None


def _external_url_from_last_step(last_apply_step_json: str | None) -> str | None:
    if not last_apply_step_json:
        return None
    try:
        step_data = json.loads(last_apply_step_json)
    except Exception:
        return None
    step = step_data.get("step") or {}
    if step.get("page_type") != "external_redirect":
        return None
    page_url = step.get("page_url")
    return page_url if isinstance(page_url, str) and page_url else None


def _fields_by_id_from_apply_payload(step_data: dict) -> dict[str, dict]:
    fields_by_id: dict[str, dict] = {}

    def remember(fields: list[dict] | None) -> None:
        for field in fields or []:
            field_id = field.get("id")
            if isinstance(field_id, str) and field_id and field_id not in fields_by_id:
                fields_by_id[field_id] = field

    remember((step_data.get("step") or {}).get("fields"))
    for entry in step_data.get("step_history") or []:
        remember((entry.get("step") or {}).get("fields"))
    return fields_by_id


@router.post("/applications/{app_id}/external_harness", response_model=dict)
async def enqueue_external_harness(
    app_id: str,
    request: Request,
    body: ExternalHarnessRequest | None = None,
):
    """Start the external apply harness from a known external portal URL."""
    repo: SqliteApplicationRepository = request.app.state.application_repository
    queue_repo = request.app.state.queue_repository

    app = await repo.get(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")

    applyable_states = {"approved", "paused", "failed"}
    if app.state not in applyable_states:
        raise HTTPException(status_code=409, detail=f"cannot start external harness from state '{app.state}'")

    target_url = (
        (body.target_url if body else None)
        or app.target_application_url
        or _external_url_from_last_step(app.last_apply_step_json)
        or app.source_url
    )
    await repo.update_target_application(
        app_id,
        target_application_url=target_url,
        target_portal=app.source_provider,
    )
    await repo.update_state(app_id, "applying")
    await queue_repo.enqueue("apply", app_id, {"external_start_url": target_url})
    return {"application_id": app_id, "state": "applying", "target_url": target_url}


@router.post("/applications/{app_id}/gate", response_model=dict)
async def enqueue_gate_resume(app_id: str, request: Request, body: GateResumeRequest):
    """Enqueue a resume after the HITL gate (user approved field values)."""
    repo: SqliteApplicationRepository = request.app.state.application_repository
    queue_repo = request.app.state.queue_repository
    cache_repo = request.app.state.question_cache_repository

    app = await repo.get(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")

    # Save human-approved answers to cache using field labels from last_apply_step_json
    if body.approved_values and app.last_apply_step_json:
        try:
            step = json.loads(app.last_apply_step_json)
            fields_by_id = _fields_by_id_from_apply_payload(step)
            for field_id, answer in body.approved_values.items():
                if field_id.startswith("__external_apply_"):
                    continue
                field_meta = fields_by_id.get(field_id)
                label = field_meta["label"] if field_meta else field_id
                field_type = field_meta["field_type"] if field_meta else None
                required = bool(field_meta.get("required")) if field_meta else False
                if label and answer is not None and (answer.strip() or not required):
                    await cache_repo.save(label, answer, field_type=field_type)
                    log.info("[gate] cached answer for label=%r answer=%r", label, answer)
        except Exception:
            log.exception("[gate] failed to save answers to cache — continuing")

    await repo.update_state(app_id, "applying")
    await queue_repo.enqueue("resume", app_id, {
        "run_id": body.run_id,
        "approved_values": body.approved_values,
        "action_label": "Continue",
        "action": "continue",
    })
    return {"application_id": app_id, "state": "applying"}


class SubmitRequest(BaseModel):
    run_id: str
    label: str = "Continue"
    corrected_values: dict[str, str] = {}  # field_label → corrected answer


@router.post("/applications/{app_id}/submit", response_model=dict)
async def enqueue_submit(app_id: str, request: Request, body: SubmitRequest):
    """Enqueue a final submit resume (user confirmed they want to submit to SEEK)."""
    repo: SqliteApplicationRepository = request.app.state.application_repository
    queue_repo = request.app.state.queue_repository
    cache_repo = request.app.state.question_cache_repository

    app = await repo.get(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")

    # Save user-corrected values to cache so future applications use them.
    # These are keyed by field label (not id) — the portal sends label→value pairs.
    if body.corrected_values and app.last_apply_step_json:
        try:
            step_data = json.loads(app.last_apply_step_json)
            # Build a label→field_type map from all steps in step_history
            field_types: dict[str, str] = {}
            for entry in (step_data.get("step_history") or []):
                for f in (entry.get("step", {}).get("fields") or []):
                    if f.get("label"):
                        field_types[f["label"]] = f.get("field_type", "text")
            for label, answer in body.corrected_values.items():
                if label and answer is not None and answer.strip():
                    await cache_repo.save(label, answer, field_type=field_types.get(label))
                    log.info("[submit] cached corrected answer label=%r answer=%r", label, answer)
        except Exception:
            log.exception("[submit] failed to save corrected answers to cache — continuing")

    await repo.update_state(app_id, "submitting")
    await queue_repo.enqueue("resume", app_id, {
        "run_id": body.run_id,
        "approved_values": {},
        "action_label": body.label,
        "action": "continue",
    })
    return {"application_id": app_id, "state": "submitting"}
