"""LangGraph prepare workflow.

Nodes:
  fetch_detail  → fetch full job description from tools/
  generate      → AI cover letter + predicted questions
  persist       → save application + drafts to SQLite

The graph is a linear chain (no branching in Phase 2).
HITL gate: the portal approves/discards after preparation; the workflow
itself only creates the draft and marks state="prepared".
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langgraph.graph import END, StateGraph

log = logging.getLogger("prepare")

from app.persistence.sqlite.applications import SqliteApplicationRepository, SqliteDraftRepository
from app.persistence.sqlite.job_analysis import SqliteJobAnalysisRepository
from app.persistence.sqlite.jobs import SqliteJobRepository
from app.settings import Settings
from app.state.prepare import PrepareState
from app.providers import registry
from app.services.run_events import emit as _emit, set_node as _set_node
from app.tools.client import ToolClient
from app.workflows.cover_letter import run_cover_letter


def build_prepare_graph(
    settings: Settings,
    tool_client: ToolClient,
    job_repo: SqliteJobRepository,
    app_repo: SqliteApplicationRepository,
    draft_repo: SqliteDraftRepository,
    analysis_repo: SqliteJobAnalysisRepository | None = None,
    existing_app_id: str | None = None,
):
    profile = _load_profile(settings)

    async def fetch_detail(state: PrepareState) -> dict[str, Any]:
        _set_node("fetch_detail")
        log.info("[fetch_detail] job_id=%s", state.job_id)
        _emit("node", "fetch_detail: fetching job description", {"job_id": state.job_id})
        job = await job_repo.get(state.job_id)
        if job is None:
            log.warning("[fetch_detail] job_id=%s not found in database", state.job_id)
            return {"error": f"job {state.job_id} not found in database"}

        # Extract provider_job_id from payload
        provider_job_id = str(job.payload.get("provider_job_id", ""))
        if not provider_job_id:
            log.warning("[fetch_detail] job_id=%s has no provider_job_id", state.job_id)
            return {"error": f"job {state.job_id} has no provider_job_id in payload"}

        log.info("[fetch_detail] fetching detail for provider=%s provider_job_id=%s title=%s", job.provider, provider_job_id, job.title)
        adapter = registry.get(job.provider)
        detail = await adapter.fetch_detail(tool_client, provider_job_id)
        log.info("[fetch_detail] done title=%s company=%s desc_len=%d",
                 detail.title, detail.company, len(detail.description))
        _emit("node", f"fetch_detail: got JD — {detail.title} @ {detail.company}", {"title": detail.title, "company": detail.company, "desc_len": len(detail.description)})
        return {"detail": detail}

    async def generate(state: PrepareState) -> dict[str, Any]:
        _set_node("generate")
        if state.error or state.detail is None:
            log.warning("[generate] skipping — error=%s detail=%s", state.error, state.detail)
            return {}

        # Look up pre-parsed JD analysis from cache
        cached = None
        if analysis_repo is not None:
            cached = await analysis_repo.get(state.job_id)
            if cached:
                log.info("[generate] using cached JD analysis for job=%s (skipping parse_jd LLM call)", state.detail.title)
            else:
                log.info("[generate] no cached analysis for job=%s, parse_jd will run", state.detail.title)

        log.info("[generate] starting cover letter for job=%s", state.detail.title)
        _emit("node", f"generate: writing cover letter for {state.detail.title}", {"job": state.detail.title, "company": state.detail.company})
        cl_result = await run_cover_letter(
            settings, job=state.detail, profile=profile, cached_analysis=cached
        )
        log.info("[generate] cover_letter: suitable=%s gaps=%s words=%d evidence_lines=%d",
                 cl_result.is_suitable, cl_result.gaps, len(cl_result.cover_letter.split()),
                 len(cl_result.evidence.splitlines()))
        _emit("node", f"generate: done — suitable={cl_result.is_suitable} fit={cl_result.fit_score} words={len(cl_result.cover_letter.split())}", {"is_suitable": cl_result.is_suitable, "fit_score": cl_result.fit_score, "gaps": cl_result.gaps, "words": len(cl_result.cover_letter.split())})
        return {
            "cover_letter": cl_result.cover_letter,
            "is_suitable": cl_result.is_suitable,
            "gaps": cl_result.gaps,
            "fit_score": cl_result.fit_score,
            "match_evidence": cl_result.evidence,
        }

    async def persist(state: PrepareState) -> dict[str, Any]:
        _set_node("persist")
        if state.error:
            log.warning("[persist] skipping — error=%s", state.error)
            if existing_app_id:
                await app_repo.update_state(existing_app_id, "failed")
            return {}

        job = await job_repo.get(state.job_id)
        if job is None:
            log.warning("[persist] job_id=%s not found", state.job_id)
            return {"error": f"job {state.job_id} not found"}

        if existing_app_id:
            # Check if the app was cancelled while prepare was in-flight.
            # If so, skip the update — the cancel already set the terminal state.
            current = await app_repo.get(existing_app_id)
            if current is not None and current.state == "discarded":
                log.info("[persist] app was cancelled mid-flight — skipping update application_id=%s", existing_app_id)
                return {"error": "application cancelled during prepare"}

            # Update the pre-created "preparing" shell
            new_state = "prepared" if state.is_suitable else "unsuitable"
            await app_repo.update_after_prepare(
                existing_app_id,
                is_suitable=state.is_suitable,
                gaps=state.gaps,
                fit_score=state.fit_score,
                new_state=new_state,
            )
            app_id = existing_app_id
            # Ensure job stays in_review even if cancel briefly reset it.
            await job_repo.update_state(state.job_id, "in_review")
            log.info("[persist] updated application_id=%s for job=%s suitable=%s state=%s",
                     app_id, state.job_id, state.is_suitable, new_state)
        else:
            # Old path: create new application
            app_id = await app_repo.create(
                job_id=state.job_id,
                source_provider=job.provider,
                source_url=job.source_url,
                is_suitable=state.is_suitable,
                gaps=state.gaps,
                fit_score=state.fit_score,
            )
            log.info("[persist] created application_id=%s for job=%s suitable=%s", app_id, state.job_id, state.is_suitable)

        if not state.is_suitable:
            log.info("[persist] not suitable — skipping drafts (gaps=%s)", state.gaps)
            # Still save match_evidence so cache-hit path can return it
            if state.match_evidence:
                await draft_repo.create(
                    application_id=app_id,
                    draft_type="match_evidence",
                    generator=settings.openai_model,
                    content=state.match_evidence,
                )
            return {"application_id": app_id}

        await draft_repo.create(
            application_id=app_id,
            draft_type="cover_letter",
            generator=settings.openai_model,
            content=state.cover_letter,
        )

        if state.match_evidence:
            await draft_repo.create(
                application_id=app_id,
                draft_type="match_evidence",
                generator=settings.openai_model,
                content=state.match_evidence,
            )

        for qa in state.questions:
            import hashlib
            fingerprint = hashlib.md5(qa["question"].encode()).hexdigest()
            await draft_repo.create(
                application_id=app_id,
                draft_type="question_answer",
                question_fingerprint=fingerprint,
                generator=settings.openai_model,
                content=json.dumps(qa),
            )

        log.info("[persist] saved cover_letter + %d Q&A drafts for application_id=%s",
                 len(state.questions), app_id)
        return {"application_id": app_id}

    graph = StateGraph(PrepareState)
    graph.add_node("fetch_detail", fetch_detail)
    graph.add_node("generate", generate)
    graph.add_node("persist", persist)

    graph.set_entry_point("fetch_detail")
    graph.add_edge("fetch_detail", "generate")
    graph.add_edge("generate", "persist")
    graph.add_edge("persist", END)

    return graph.compile()


async def run_prepare(
    settings: Settings,
    tool_client: ToolClient,
    job_repo: SqliteJobRepository,
    app_repo: SqliteApplicationRepository,
    draft_repo: SqliteDraftRepository,
    analysis_repo: SqliteJobAnalysisRepository | None = None,
    *,
    job_id: str,
    existing_app_id: str | None = None,
) -> PrepareState:
    if existing_app_id is None:
        # Old cache-hit path (only used for sync /workflows/prepare endpoint)
        cached = await app_repo.get_active_by_job_id(job_id)
        if cached:
            app_id, is_suitable, gaps, fit_score = cached
            match_evidence = await draft_repo.get_match_evidence(app_id)
            if match_evidence:
                cover_letter = await draft_repo.get_cover_letter(app_id)
                log.info("[run_prepare] cache hit job_id=%s application_id=%s", job_id, app_id)
                return PrepareState(
                    job_id=job_id,
                    application_id=app_id,
                    cover_letter=cover_letter,
                    match_evidence=match_evidence,
                    is_suitable=is_suitable,
                    gaps=gaps,
                    fit_score=fit_score,
                    detail=None,
                )
            # No match_evidence — old application record. Discard it and re-prepare.
            log.info("[run_prepare] cache hit but no match_evidence — discarding old record and re-preparing job_id=%s app_id=%s", job_id, app_id)
            await app_repo.update_state(app_id, "discarded")

    graph = build_prepare_graph(
        settings, tool_client, job_repo, app_repo, draft_repo, analysis_repo,
        existing_app_id=existing_app_id,
    )
    initial = PrepareState(job_id=job_id)
    result = await graph.ainvoke(initial)
    return PrepareState.model_validate(result)


def _load_profile(settings: Settings) -> dict:
    target_path = settings.resolved_target_profile_path
    if target_path.exists():
        return json.loads(target_path.read_text(encoding="utf-8"))

    path = settings.resolved_profile_path
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
