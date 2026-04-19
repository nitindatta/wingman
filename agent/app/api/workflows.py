import uuid

from fastapi import APIRouter, HTTPException, Request

from pydantic import BaseModel

from app.services.ai import predict_questions
from app.state.apply import ApplyRequest, ApplyResumeRequest, ApplyStepResponse
from app.state.jobs import SearchRequest, SearchResponse
from app.state.prepare import PrepareRequest, PrepareResponse
from app.providers import registry
from app.tools.indeed import IndeedDriftError, IndeedToolError
from app.tools.seek import SeekDriftError, SeekToolError
from app.tools.indeed_detail import IndeedDetailDriftError, IndeedDetailError
from app.tools.seek_detail import SeekDetailDriftError, SeekDetailError
from app.workflows.apply import resume_apply, run_apply
from app.workflows.prepare import run_prepare
from app.workflows.search import run_search

router = APIRouter()


@router.post("/workflows/prepare", response_model=PrepareResponse)
async def start_prepare(request: Request, body: PrepareRequest) -> PrepareResponse:
    try:
        state = await run_prepare(
            request.app.state.settings,
            request.app.state.tool_client,
            request.app.state.job_repository,
            request.app.state.application_repository,
            request.app.state.draft_repository,
            request.app.state.job_analysis_repository,
            job_id=body.job_id,
        )
    except SeekDetailDriftError as exc:
        raise HTTPException(status_code=503, detail=f"seek detail drift: {exc.reason}")
    except SeekDetailError as exc:
        raise HTTPException(status_code=502, detail=f"seek detail error: {exc.error.type}")
    except IndeedDetailDriftError as exc:
        raise HTTPException(status_code=503, detail=f"indeed detail drift: {exc.reason}")
    except IndeedDetailError as exc:
        raise HTTPException(status_code=502, detail=f"indeed detail error: {exc.error.type}")

    if state.error:
        raise HTTPException(status_code=422, detail=state.error)

    # Job description: from workflow detail, or fall back to job_analysis cache
    job_description = ""
    if state.detail:
        job_description = state.detail.description
    else:
        analysis = await request.app.state.job_analysis_repository.get(body.job_id)
        if analysis:
            job_description = analysis.description

    return PrepareResponse(
        application_id=state.application_id,
        cover_letter=state.cover_letter,
        job_description=job_description,
        questions=state.questions,
        is_suitable=state.is_suitable,
        gaps=state.gaps,
        fit_score=state.fit_score,
        match_evidence=state.match_evidence,
    )


class QuestionsRequest(BaseModel):
    application_id: str


class QuestionsResponse(BaseModel):
    questions: list[dict[str, str]]


@router.post("/workflows/questions", response_model=QuestionsResponse)
async def generate_questions(request: Request, body: QuestionsRequest) -> QuestionsResponse:
    import json
    app_repo = request.app.state.application_repository
    job_repo = request.app.state.job_repository
    draft_repo = request.app.state.draft_repository

    app = await app_repo.get(body.application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")

    job = await job_repo.get(app.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    settings = request.app.state.settings
    profile_path = (
        settings.resolved_target_profile_path
        if settings.resolved_target_profile_path.exists()
        else settings.resolved_profile_path
    )
    profile = json.loads(profile_path.read_text(encoding="utf-8"))

    from app.tools.seek_detail import fetch_job_detail
    provider_job_id = str(job.payload.get("provider_job_id", ""))
    detail = await fetch_job_detail(request.app.state.tool_client, job_id=provider_job_id)

    questions = await predict_questions(settings, job=detail, profile=profile)
    return QuestionsResponse(questions=questions)


@router.post("/workflows/apply", response_model=ApplyStepResponse)
async def start_apply(request: Request, body: ApplyRequest) -> ApplyStepResponse:
    run_repo = request.app.state.workflow_run_repository
    run_id = await run_repo.create(application_id=body.application_id, workflow_type="apply")

    state = await run_apply(
        request.app.state.settings,
        request.app.state.tool_client,
        request.app.state.application_repository,
        request.app.state.draft_repository,
        run_repo,
        request.app.state.browser_session_repository,
        request.app.state.database.connection,
        application_id=body.application_id,
        workflow_run_id=run_id,
    )

    return _apply_response(state)


@router.post("/workflows/apply/{run_id}/resume", response_model=ApplyStepResponse)
async def resume_apply_run(run_id: str, request: Request, body: ApplyResumeRequest) -> ApplyStepResponse:
    state = await resume_apply(
        request.app.state.settings,
        request.app.state.tool_client,
        request.app.state.application_repository,
        request.app.state.draft_repository,
        request.app.state.workflow_run_repository,
        request.app.state.browser_session_repository,
        request.app.state.database.connection,
        workflow_run_id=run_id,
        approved_values=body.approved_values,
        action_label=body.action_label,
        action=body.action,
    )

    return _apply_response(state)


def _apply_response(state) -> ApplyStepResponse:
    # Translate LangGraph's "running" status to a meaningful portal status.
    # When the graph pauses at the gate interrupt, status stays "running" but
    # low_confidence_ids is populated — surface this as "needs_review" so the
    # portal knows to show only the uncertain fields for human input.
    status = state.status
    if status == "running" and state.low_confidence_ids:
        status = "needs_review"

    return ApplyStepResponse(
        workflow_run_id=state.workflow_run_id,
        status=status,
        step=state.current_step,
        proposed_values=state.proposed_values,
        low_confidence_ids=state.low_confidence_ids,
        submit_action_label=state.submit_action_label,
        step_history=state.step_history,
        error=state.error,
        pause_reason=state.pause_reason,
    )


@router.post("/workflows/search", response_model=SearchResponse)
async def start_search(request: Request, body: SearchRequest) -> SearchResponse:
    if body.provider not in registry.names():
        raise HTTPException(status_code=400, detail=f"unsupported provider: {body.provider}")
    try:
        state = await run_search(
            request.app.state.tool_client,
            request.app.state.job_repository,
            keywords=body.keywords,
            location=body.location,
            max_pages=body.max_pages,
            provider=body.provider,
        )
    except SeekDriftError as exc:
        raise HTTPException(status_code=503, detail=f"seek parser drift: {exc.drift.parser_id}")
    except SeekToolError as exc:
        raise HTTPException(status_code=502, detail=f"seek tool error: {exc.error.type}")
    except IndeedDriftError as exc:
        raise HTTPException(status_code=503, detail=f"indeed parser drift: {exc.drift.parser_id}")
    except IndeedToolError as exc:
        raise HTTPException(status_code=502, detail=f"indeed tool error: {exc.error.type}")

    return SearchResponse(
        discovered=len(state.discovered) + len(state.blocked),
        blocked=len(state.blocked),
        persisted=len(state.persisted_job_ids),
        job_ids=state.persisted_job_ids,
    )
