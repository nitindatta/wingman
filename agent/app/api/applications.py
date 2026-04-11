"""Application and draft API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.persistence.sqlite.applications import SqliteApplicationRepository, SqliteDraftRepository
from app.state.prepare import Application, Draft, PrepareRequest, PrepareResponse
from app.workflows.prepare import run_prepare

router = APIRouter()


@router.get("/applications", response_model=dict)
async def list_applications(request: Request, limit: int = 50, state: str | None = None):
    app_repo: SqliteApplicationRepository = request.app.state.application_repository
    job_repo = request.app.state.job_repository
    apps = await app_repo.list_all(limit=limit, state=state)

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
        })
    return {"applications": results}


@router.get("/applications/{app_id}", response_model=dict)
async def get_application(app_id: str, request: Request):
    app_repo: SqliteApplicationRepository = request.app.state.application_repository
    draft_repo: SqliteDraftRepository = request.app.state.draft_repository

    app = await app_repo.get(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")

    drafts = await draft_repo.list_for_application(app_id)
    return {"application": app.model_dump(), "drafts": [d.model_dump() for d in drafts]}


class ApproveRequest(BaseModel):
    cover_letter: str | None = None


@router.post("/applications/{app_id}/approve", response_model=dict)
async def approve_application(app_id: str, request: Request, body: ApproveRequest = ApproveRequest()):
    repo: SqliteApplicationRepository = request.app.state.application_repository
    draft_repo: SqliteDraftRepository = request.app.state.draft_repository
    app = await repo.get(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")
    if app.state not in ("prepared",):
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
