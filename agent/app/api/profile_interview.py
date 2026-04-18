"""Profile interview API routes."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, HTTPException, Request

from app.services.profile_ingest import load_raw_profile
from app.services.profile_target import (
    build_canonical_profile,
    build_canonical_profile_from_raw_profile,
)
from app.state.canonical_profile import CanonicalProfile
from app.state.profile_interview import (
    AnswerProfileInterviewRequest,
    ApproveProfileInterviewRequest,
    CompleteProfileInterviewRequest,
    DeferProfileInterviewRequest,
    ProfileInterviewPrompt,
    ProfileInterviewSessionResponse,
    ProfileInterviewState,
    SelectProfileInterviewRequest,
    StartProfileInterviewRequest,
)
from app.workflows.profile_interview import run_profile_interview

router = APIRouter()


@router.get("/api/profile-interview/active", response_model=ProfileInterviewSessionResponse | None)
async def get_active_profile_interview(request: Request) -> ProfileInterviewSessionResponse | None:
    state = await request.app.state.profile_interview_repository.get_active()
    if state is None:
        return None
    return _response_from_state(state)


@router.post("/api/profile-interview/start", response_model=ProfileInterviewSessionResponse)
async def start_profile_interview(
    request: Request,
    body: StartProfileInterviewRequest,
) -> ProfileInterviewSessionResponse:
    repo = request.app.state.profile_interview_repository
    existing = await repo.get_active()
    if existing is not None:
        return _response_from_state(existing)

    settings = request.app.state.settings
    canonical_profile = _load_or_build_target_profile(settings)
    if canonical_profile is None:
        raise HTTPException(status_code=404, detail="no source profile is available yet")

    source_profile_path = str(
        settings.resolved_raw_profile_path
        if settings.resolved_raw_profile_path.exists()
        else settings.resolved_profile_path
    )
    state = ProfileInterviewState(
        session_id=str(uuid.uuid4()),
        source_profile_path=source_profile_path,
        target_profile_path=str(settings.resolved_target_profile_path),
        canonical_profile=canonical_profile,
        action="select" if body.item_id else "start",
        selected_item_id=body.item_id or "",
    )
    state = await run_profile_interview(settings, state)
    await repo.create(state)
    await _persist_draft_snapshot(repo, state)
    return _response_from_state(state)


@router.post("/api/profile-interview/{session_id}/select", response_model=ProfileInterviewSessionResponse)
async def select_profile_interview_item(
    session_id: str,
    request: Request,
    body: SelectProfileInterviewRequest,
) -> ProfileInterviewSessionResponse:
    repo = request.app.state.profile_interview_repository
    existing = await repo.get(session_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="profile interview session not found")

    state = existing.model_copy(deep=True)
    _refresh_state_from_target_profile(request.app.state.settings, state)
    state.action = "select"
    state.selected_item_id = body.item_id
    state.error = None
    updated = await run_profile_interview(request.app.state.settings, state)

    await repo.save_state(updated)
    await _persist_draft_snapshot(repo, updated)
    return _response_from_state(updated)


@router.post("/api/profile-interview/{session_id}/answer", response_model=ProfileInterviewSessionResponse)
async def answer_profile_interview(
    session_id: str,
    request: Request,
    body: AnswerProfileInterviewRequest,
) -> ProfileInterviewSessionResponse:
    repo = request.app.state.profile_interview_repository
    existing = await repo.get(session_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="profile interview session not found")

    state = existing.model_copy(deep=True)
    _refresh_state_from_target_profile(request.app.state.settings, state)
    state.action = "answer"
    state.user_answer = body.answer
    state.error = None
    updated = await run_profile_interview(request.app.state.settings, state)

    await repo.record_turn(
        session_id=updated.session_id,
        item_id=updated.current_item_id or existing.current_item_id,
        question_id=existing.current_question_id,
        question_text=existing.current_question,
        user_answer=body.answer,
        interpreted_answer=updated.last_interpretation,
    )
    _persist_target_profile(request.app.state.settings, updated.canonical_profile)
    await repo.save_state(updated)
    await _persist_draft_snapshot(repo, updated)
    return _response_from_state(updated)


@router.post("/api/profile-interview/{session_id}/approve", response_model=ProfileInterviewSessionResponse)
async def approve_profile_interview_item(
    session_id: str,
    request: Request,
    body: ApproveProfileInterviewRequest,
) -> ProfileInterviewSessionResponse:
    del body
    repo = request.app.state.profile_interview_repository
    existing = await repo.get(session_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="profile interview session not found")

    state = existing.model_copy(deep=True)
    _refresh_state_from_target_profile(request.app.state.settings, state)
    state.action = "approve"
    state.error = None
    updated = await run_profile_interview(request.app.state.settings, state)

    _persist_target_profile(request.app.state.settings, updated.canonical_profile)
    await repo.save_state(updated)
    await _persist_draft_snapshot(repo, updated)
    return _response_from_state(updated)


@router.post("/api/profile-interview/{session_id}/defer", response_model=ProfileInterviewSessionResponse)
async def defer_profile_interview_item(
    session_id: str,
    request: Request,
    body: DeferProfileInterviewRequest,
) -> ProfileInterviewSessionResponse:
    del body
    repo = request.app.state.profile_interview_repository
    existing = await repo.get(session_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="profile interview session not found")

    state = existing.model_copy(deep=True)
    _refresh_state_from_target_profile(request.app.state.settings, state)
    state.action = "defer"
    state.error = None
    updated = await run_profile_interview(request.app.state.settings, state)

    _persist_target_profile(request.app.state.settings, updated.canonical_profile)
    await repo.save_state(updated)
    await _persist_draft_snapshot(repo, updated)
    return _response_from_state(updated)


@router.post("/api/profile-interview/{session_id}/complete", response_model=ProfileInterviewSessionResponse)
async def complete_profile_interview(
    session_id: str,
    request: Request,
    body: CompleteProfileInterviewRequest,
) -> ProfileInterviewSessionResponse:
    del body
    repo = request.app.state.profile_interview_repository
    existing = await repo.get(session_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="profile interview session not found")

    state = existing.model_copy(deep=True)
    _refresh_state_from_target_profile(request.app.state.settings, state)
    state.action = "complete"
    state.error = None
    updated = await run_profile_interview(request.app.state.settings, state)

    _persist_target_profile(request.app.state.settings, updated.canonical_profile)
    await repo.save_state(updated)
    await _persist_draft_snapshot(repo, updated)
    return _response_from_state(updated)


def _response_from_state(state: ProfileInterviewState) -> ProfileInterviewSessionResponse:
    approved_items = sum(
        1 for item in state.canonical_profile.evidence_items if item.confidence == "approved"
    )
    return ProfileInterviewSessionResponse(
        session_id=state.session_id,
        status=state.status,
        source_profile_path=state.source_profile_path,
        target_profile_path=state.target_profile_path,
        current_item_id=state.current_item_id,
        draft_item=state.draft_item,
        open_gaps=state.open_gaps,
        current_gap=state.current_gap,
        current_question_id=state.current_question_id,
        current_question=state.current_question,
        current_prompt=state.current_prompt or ProfileInterviewPrompt(),
        last_answer_assessment=state.last_answer_assessment,
        item_quality_scores=state.item_quality_scores,
        completeness_score=state.completeness_score,
        overall_answer_quality_score=state.overall_answer_quality_score,
        overall_profile_score=state.overall_profile_score,
        approved_items=approved_items,
        total_items=len(state.canonical_profile.evidence_items),
        error=state.error,
    )


async def _persist_draft_snapshot(repo, state: ProfileInterviewState) -> None:
    if state.draft_item is None or not state.current_item_id:
        return
    await repo.record_draft(
        session_id=state.session_id,
        item_id=state.current_item_id,
        status=state.status,
        completeness_score=state.completeness_score,
        item_json=state.draft_item.model_dump_json(),
        gap_summary_json=json.dumps(state.open_gaps),
    )


def _load_or_build_target_profile(settings) -> CanonicalProfile | None:
    source_path = settings.resolved_profile_path
    target_path = settings.resolved_target_profile_path
    raw_profile = load_raw_profile(settings)

    if raw_profile is None and not source_path.exists():
        return None

    if target_path.exists():
        return CanonicalProfile.model_validate_json(target_path.read_text(encoding="utf-8"))

    if raw_profile is not None:
        return build_canonical_profile_from_raw_profile(raw_profile)

    legacy_profile = json.loads(source_path.read_text(encoding="utf-8"))
    return build_canonical_profile(legacy_profile)


def _persist_target_profile(settings, profile: CanonicalProfile) -> None:
    target_path = settings.resolved_target_profile_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")


def _refresh_state_from_target_profile(settings, state: ProfileInterviewState) -> None:
    refreshed = _load_or_build_target_profile(settings)
    if refreshed is None:
        return
    state.canonical_profile = refreshed
    active_item_id = state.selected_item_id or state.current_item_id
    if not active_item_id:
        state.draft_item = None
        return
    state.draft_item = next(
        (item.model_copy(deep=True) for item in refreshed.evidence_items if item.id == active_item_id),
        None,
    )
