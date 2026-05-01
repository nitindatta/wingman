"""Custom harness shell for external apply pages."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

from app.persistence.sqlite.question_cache import SqliteQuestionCacheRepository
from app.services.external_apply_ai import propose_external_apply_action, propose_external_apply_actions
from app.services.run_events import emit as _emit
from app.services.external_apply_policy import (
    should_default_check_consent_field,
    validate_external_apply_action,
)
from app.settings import Settings
from app.state.external_apply import (
    ActionResult,
    ActionTrace,
    ExternalApplyMemoryContext,
    ExternalApplyState,
    HarnessStatus,
    PageObservation,
    PlanningFrame,
    PolicyDecision,
    ProposedAction,
    UserQuestion,
)
from app.tools.browser_client import execute_external_apply_action, observe_external_apply
from app.tools.client import ToolClient

EXTERNAL_USER_ANSWER_KEY = "__external_apply_user_answer"
EXTERNAL_USER_ANSWER_PREFIX = "__external_apply_user_answer__"
EXTERNAL_USER_QUESTION_PREFIX = "__external_apply_user_question__"

ObserveFn = Callable[[ToolClient, str], Awaitable[PageObservation]]
PlanFn = Callable[..., Awaitable[ProposedAction]]
BatchPlanFn = Callable[..., Awaitable[list[ProposedAction]]]
PolicyFn = Callable[..., PolicyDecision]
ExecuteFn = Callable[[ToolClient, str, ProposedAction], Awaitable[ActionResult]]
SleepFn = Callable[[float], Awaitable[None]]
MAX_TRANSACTION_PASSES = 3


async def plan_external_apply_step(
    settings: Settings,
    tool_client: ToolClient,
    *,
    session_key: str,
    application_id: str,
    profile_facts: dict[str, Any],
    approved_memory: list[dict[str, Any]] | None = None,
    recent_actions: list[ActionTrace] | None = None,
    question_cache: SqliteQuestionCacheRepository | None = None,
    observe_fn: ObserveFn = observe_external_apply,
    planner_fn: PlanFn = propose_external_apply_action,
) -> ExternalApplyState:
    """Observe a page and propose one next action without executing it."""

    memory = approved_memory or []
    traces = recent_actions or []
    observation = await observe_fn(tool_client, session_key)
    memory_context = _derive_external_memory_context(observation, profile_facts, traces)
    planning_frame = _build_planning_frame(observation, profile_facts, memory_context)
    effective_memory = _approved_memory_with_recent_answers(
        [*memory, *await _cached_approved_memory_for_observation(observation, question_cache)],
        traces,
    )
    proposed_action = await planner_fn(
        settings,
        observation=observation,
        profile_facts=profile_facts,
        approved_memory=effective_memory,
        recent_actions=traces,
        memory_context=memory_context.model_dump(),
        planning_frame=planning_frame.model_dump(),
    )

    pending_questions = _user_questions_for_action(proposed_action, observation)
    return ExternalApplyState(
        application_id=application_id,
        current_url=observation.url,
        page_type=observation.page_type,
        observation=observation,
        proposed_action=proposed_action,
        memory_context=memory_context,
        planning_frame=planning_frame,
        completed_actions=traces,
        status=_status_for_proposed_action(proposed_action),
        submit_ready=proposed_action.action_type == "stop_ready_to_submit",
        pending_user_question=pending_questions[0] if pending_questions else None,
        pending_user_questions=pending_questions,
    )


def _status_for_proposed_action(action: ProposedAction) -> HarnessStatus:
    if action.action_type == "ask_user":
        return "paused_for_user"
    if action.action_type == "stop_ready_to_submit":
        return "ready_to_submit"
    if action.action_type == "stop_failed":
        return "failed"
    return "running"

async def run_external_apply_step(
    settings: Settings,
    tool_client: ToolClient,
    *,
    session_key: str,
    application_id: str,
    profile_facts: dict[str, Any],
    approved_memory: list[dict[str, Any]] | None = None,
    recent_actions: list[ActionTrace] | None = None,
    question_cache: SqliteQuestionCacheRepository | None = None,
    observe_fn: ObserveFn = observe_external_apply,
    planner_fn: PlanFn = propose_external_apply_action,
    batch_planner_fn: BatchPlanFn | None = None,
    policy_fn: PolicyFn = validate_external_apply_action,
    execute_fn: ExecuteFn = execute_external_apply_action,
    sleep_fn: SleepFn = asyncio.sleep,
) -> ExternalApplyState:
    """Run one observe-plan-policy-execute step or one safe page-level batch.

    The action is executed only when the deterministic policy returns
    decision="allowed". Paused/rejected actions are returned as state for the
    portal or workflow to handle.
    """
    memory = approved_memory or []
    completed_actions = list(recent_actions or [])
    current_url = (
        completed_actions[-1].result.new_url
        if completed_actions and completed_actions[-1].result and completed_actions[-1].result.new_url
        else ""
    )
    last_result: ActionResult | None = None
    observation = await observe_fn(tool_client, session_key)

    for transaction_pass in range(MAX_TRANSACTION_PASSES):
        memory_context = _derive_external_memory_context(observation, profile_facts, completed_actions)
        planning_frame = _build_planning_frame(observation, profile_facts, memory_context)
        _emit("observe", f"observe: {observation.page_type} @ {observation.url[:70]}", {
            "url": observation.url,
            "page_type": observation.page_type,
            "fields_count": len(observation.fields),
            "buttons_count": len(observation.buttons),
            "fields": [
                {
                    "id": f.element_id,
                    "label": f.label,
                    "type": f.field_type,
                    "control_kind": f.control_kind,
                    "invalid": f.invalid,
                    "validation_message": f.validation_message,
                }
                for f in observation.fields[:10]
            ],
            "buttons": [{"id": b.element_id, "label": b.label} for b in observation.buttons[:6]],
            "visible_text": (observation.visible_text or "")[:200],
            "transaction_pass": transaction_pass + 1,
            "memory_context": memory_context.model_dump(),
            "planning_frame": planning_frame.model_dump(),
        })

        actions = await _planned_actions_for_observation(
            settings,
            observation=observation,
            profile_facts=profile_facts,
            approved_memory=memory,
            recent_actions=completed_actions,
            memory_context=memory_context,
            planning_frame=planning_frame,
            question_cache=question_cache,
            planner_fn=planner_fn,
            batch_planner_fn=batch_planner_fn,
        )
        if not actions:
            actions = [
                ProposedAction(
                    action_type="ask_user",
                    question="I could not determine a safe next action on this page. What should I do next?",
                    confidence=0.75,
                    risk="medium",
                    reason="Planner returned no actions.",
                    source="page",
                )
            ]

        last_state: ExternalApplyState | None = None
        mutated_current_page = False
        delayed_transition_observation: PageObservation | None = None

        for index, action in enumerate(actions):
            _emit("plan", f"plan: {action.action_type}", {
                "action_type": action.action_type,
                "element_id": action.element_id,
                "value": (action.value or "")[:80],
                "confidence": action.confidence,
                "risk": action.risk,
                "reason": action.reason,
                "source": action.source,
                "question": action.question,
            })
            if action.action_type in {"click", "stop_ready_to_submit", "stop_failed"} and mutated_current_page and last_state is not None:
                break

            pending_questions = _user_questions_for_action(action, observation)
            planned = ExternalApplyState(
                application_id=application_id,
                current_url=current_url or observation.url,
                page_type=observation.page_type,
                observation=observation,
                proposed_action=action,
                memory_context=memory_context,
                planning_frame=planning_frame,
                completed_actions=completed_actions,
                status=_status_for_proposed_action(action),
                submit_ready=action.action_type == "stop_ready_to_submit",
                pending_user_question=pending_questions[0] if pending_questions else None,
                pending_user_questions=pending_questions,
                last_action_result=last_result,
            )
            planned = _apply_default_safe_action(planned, profile_facts)
            planned = _coerce_noncritical_select_option(planned)
            planned = _coerce_login_terminal_action(planned)
            if planned.proposed_action is None:
                return planned

            delayed_transition_observation = await _observe_delayed_transition_after_repeated_click(
                observation,
                planned.proposed_action,
                completed_actions,
                tool_client,
                session_key,
                observe_fn,
                sleep_fn,
            )
            if delayed_transition_observation is not None:
                current_url = delayed_transition_observation.url or current_url or observation.url
                last_state = planned.model_copy(
                    update={
                        "current_url": current_url,
                        "page_type": delayed_transition_observation.page_type,
                        "observation": delayed_transition_observation,
                        "last_action_result": last_result,
                        "status": "running",
                        "error": None,
                        "pending_user_question": None,
                        "pending_user_questions": [],
                        "submit_ready": False,
                    }
                )
                break

            required_field_questions = _required_field_questions_before_click(
                observation,
                planned.proposed_action,
                actions[index + 1 :],
            )
            if required_field_questions:
                trace = ActionTrace(
                    observation=observation,
                    proposed_action=planned.proposed_action,
                    policy_decision="paused",
                    result=None,
                )
                return planned.model_copy(
                    update={
                        "completed_actions": [*completed_actions, trace],
                        "status": "paused_for_user",
                        "pending_user_question": required_field_questions[0],
                        "pending_user_questions": required_field_questions,
                        "risk_flags": [*planned.risk_flags, "required_fields_incomplete"],
                        "submit_ready": False,
                        "error": None,
                    }
                )

            stale_click_question = _stale_repeated_click_question(
                observation,
                planned.proposed_action,
                completed_actions,
            )
            if stale_click_question is not None:
                trace = ActionTrace(
                    observation=observation,
                    proposed_action=planned.proposed_action,
                    policy_decision="paused",
                    result=None,
                )
                return planned.model_copy(
                    update={
                        "completed_actions": [*completed_actions, trace],
                        "status": "paused_for_user",
                        "pending_user_question": stale_click_question,
                        "pending_user_questions": [stale_click_question],
                        "risk_flags": [*planned.risk_flags, "stale_repeated_click"],
                        "submit_ready": False,
                        "error": None,
                    }
                )

            policy = policy_fn(
                observation=observation,
                proposed_action=planned.proposed_action,
                profile_facts=profile_facts,
            )
            _emit("policy", f"policy: {policy.decision}", {
                "decision": policy.decision,
                "pause_reason": policy.pause_reason,
                "risk_flags": policy.risk_flags,
                "reason": policy.reason,
            })

            if policy.decision != "allowed":
                pending_questions = _user_questions_for_pause(
                    observation,
                    planned.proposed_action,
                    actions[index + 1 :],
                    policy,
                )
                trace = ActionTrace(
                    observation=observation,
                    proposed_action=planned.proposed_action,
                    policy_decision=policy.decision,
                    result=None,
                )
                return planned.model_copy(
                    update={
                        "completed_actions": [*completed_actions, trace],
                        "risk_flags": policy.risk_flags,
                        "status": _status_for_policy_pause(policy, planned.proposed_action),
                        "pending_user_question": pending_questions[0] if pending_questions else _user_question_for_policy(policy, planned.proposed_action, observation),
                        "pending_user_questions": pending_questions,
                        "submit_ready": policy.pause_reason == "final_submit",
                        "error": policy.reason if policy.decision == "rejected" else None,
                    }
                )

            result = await execute_fn(tool_client, session_key, planned.proposed_action)
            _emit("execute", f"execute: {planned.proposed_action.action_type} -> {'ok' if result.ok else 'fail'}", {
                "action_type": planned.proposed_action.action_type,
                "element_id": planned.proposed_action.element_id,
                "value": (planned.proposed_action.value or "")[:80],
                "ok": result.ok,
                "message": result.message,
                "new_url": result.new_url,
            })
            trace = ActionTrace(
                observation=observation,
                proposed_action=planned.proposed_action,
                policy_decision="allowed",
                result=result,
            )
            completed_actions = [*completed_actions, trace]
            current_url = result.new_url or current_url or observation.url
            last_result = result
            last_state = planned.model_copy(
                update={
                    "completed_actions": completed_actions,
                    "last_action_result": result,
                    "current_url": current_url,
                    "status": "running" if result.ok else "failed",
                    "error": None if result.ok else result.message,
                    "risk_flags": result.errors,
                    "pending_user_question": None,
                    "pending_user_questions": [],
                    "submit_ready": False,
                }
            )
            if not result.ok:
                return last_state

            if planned.proposed_action.action_type in {"fill_text", "select_option", "set_checkbox", "set_radio", "upload_file"}:
                mutated_current_page = True

            if planned.proposed_action.action_type == "click":
                return last_state

        if last_state is None:
            first_action = actions[0]
            pending_questions = _user_questions_for_action(first_action, observation)
            return ExternalApplyState(
                application_id=application_id,
                current_url=current_url or observation.url,
                page_type=observation.page_type,
                observation=observation,
                proposed_action=first_action,
                memory_context=memory_context,
                planning_frame=planning_frame,
                completed_actions=completed_actions,
                status=_status_for_proposed_action(first_action),
                submit_ready=first_action.action_type == "stop_ready_to_submit",
                pending_user_question=pending_questions[0] if pending_questions else None,
                pending_user_questions=pending_questions,
                last_action_result=last_result,
            )

        if delayed_transition_observation is not None:
            observation = delayed_transition_observation
            continue

        if not mutated_current_page or transaction_pass + 1 >= MAX_TRANSACTION_PASSES:
            return last_state

        next_observation = await observe_fn(tool_client, session_key)
        current_url = next_observation.url or current_url
        if _same_page_shape(next_observation, observation):
            return last_state
        observation = next_observation

    memory_context = _derive_external_memory_context(observation, profile_facts, completed_actions)
    return ExternalApplyState(
        application_id=application_id,
        current_url=current_url or observation.url,
        page_type=observation.page_type,
        observation=observation,
        memory_context=memory_context,
        planning_frame=_build_planning_frame(observation, profile_facts, memory_context),
        completed_actions=completed_actions,
        status="running",
        last_action_result=last_result,
    )


async def _planned_actions_for_observation(
    settings: Settings,
    *,
    observation: PageObservation,
    profile_facts: dict[str, Any],
    approved_memory: list[dict[str, Any]],
    recent_actions: list[ActionTrace],
    memory_context: ExternalApplyMemoryContext,
    planning_frame: PlanningFrame,
    question_cache: SqliteQuestionCacheRepository | None,
    planner_fn: PlanFn,
    batch_planner_fn: BatchPlanFn | None,
) -> list[ProposedAction]:
    preapproved_consent_action = _preapproved_generic_consent_action(observation, recent_actions, profile_facts)
    if preapproved_consent_action is not None:
        return [preapproved_consent_action]
    preapproved_account_action = _preapproved_account_route_action(observation, memory_context)
    if preapproved_account_action is not None:
        return [preapproved_account_action]
    effective_memory = _approved_memory_with_recent_answers(
        [*approved_memory, *await _cached_approved_memory_for_observation(observation, question_cache)],
        recent_actions,
    )

    effective_batch_planner = batch_planner_fn
    if effective_batch_planner is None and planner_fn is propose_external_apply_action:
        effective_batch_planner = propose_external_apply_actions

    if effective_batch_planner is not None:
        return await effective_batch_planner(
            settings,
            observation=observation,
            profile_facts=profile_facts,
            approved_memory=effective_memory,
            recent_actions=recent_actions,
            memory_context=memory_context.model_dump(),
            planning_frame=planning_frame.model_dump(),
        )

    return [
        await planner_fn(
            settings,
            observation=observation,
            profile_facts=profile_facts,
            approved_memory=effective_memory,
            recent_actions=recent_actions,
            memory_context=memory_context.model_dump(),
            planning_frame=planning_frame.model_dump(),
        )
    ]


async def _cached_approved_memory_for_observation(
    observation: PageObservation,
    question_cache: SqliteQuestionCacheRepository | None,
) -> list[dict[str, Any]]:
    if question_cache is None:
        return []

    cached: list[dict[str, Any]] = []
    seen_labels: set[str] = set()
    for field in observation.fields:
        if not _is_cacheable_external_answer_field(field):
            continue
        label = (field.label or "").strip()
        if not label or label.lower() in seen_labels:
            continue
        seen_labels.add(label.lower())
        answer = await question_cache.find(label)
        if not answer:
            continue
        cached.append(
            {
                "label": label,
                "question": label,
                "answer": answer,
                "value": answer,
                "field_type": field.field_type,
                "source": "question_answer_cache",
                "status": "approved",
                **_question_memory_metadata(field, observation),
            }
        )
    return cached


def _derive_external_memory_context(
    observation: PageObservation,
    profile_facts: dict[str, Any],
    recent_actions: list[ActionTrace],
) -> ExternalApplyMemoryContext:
    portal_host = _portal_host(observation.url)
    portal_identity = _portal_identity(observation.url, observation.title)
    portal_account = _portal_account_record(profile_facts, portal_host)
    create_account_available = _find_create_account_action(observation) is not None
    login_attempted = _has_recent_login_click(recent_actions)
    saved_login_rejected = _saved_login_appears_rejected(
        observation,
        profile_facts,
        recent_actions,
    )
    account_mode = _portal_account_mode(profile_facts, portal_host)
    account_status = _portal_account_status(portal_account)
    account_email = _portal_account_email(profile_facts, portal_account)
    credential_available = _has_default_external_login(profile_facts) or bool(_portal_account_password(portal_account))
    credential_status = _portal_credential_status(portal_account, saved_login_rejected)
    recommendations: list[str] = []
    rejected_attempts: list[dict[str, str]] = []
    recent_failures = _recent_executor_failures(recent_actions)

    if portal_account is None and portal_host:
        recommendations.append(
            "No portal-specific account memory exists yet; treat login/account creation as scoped to this portal host."
        )
    elif account_status:
        recommendations.append(f"Portal account memory status is {account_status}.")

    if saved_login_rejected:
        rejected_attempts.append(
            {
                "kind": "external_account_login",
                "portal_host": portal_host,
                "portal_identity": portal_identity,
                "status": "rejected",
            }
        )
        if create_account_available and account_mode != "login":
            recommendations.append(
                "Saved login was rejected for this portal; prefer the observed create-account/register path instead of asking for the same password again."
            )
        else:
            recommendations.append(
                "Saved login was rejected for this portal; do not retry the same default credential without new user input."
            )

    return ExternalApplyMemoryContext(
        portal_host=portal_host,
        portal_identity=portal_identity,
        account_mode=account_mode,
        account_status=account_status,
        account_email=account_email,
        credential_available=credential_available,
        credential_status=credential_status,
        login_attempted=login_attempted,
        saved_login_rejected=saved_login_rejected,
        create_account_available=create_account_available,
        recommendations=recommendations,
        rejected_attempts=rejected_attempts,
        recent_failures=recent_failures,
    )


def _build_planning_frame(
    observation: PageObservation,
    profile_facts: dict[str, Any],
    memory_context: ExternalApplyMemoryContext,
) -> PlanningFrame:
    phase = _planning_phase(observation, memory_context)
    strategies = [
        "fill_select_check_or_upload_safe_fields_from_available_facts_or_approved_memory",
        "ask_user_for_required_fields_that_need_judgement_or_have_no_approved_answer",
        "click_navigation_only_after_current_page_required_fields_are_resolved",
        "return_stop_ready_to_submit_instead_of_final_submission",
    ]
    hints: list[str] = [*memory_context.recommendations]
    recommended_actions: list[dict[str, object]] = []
    blocked_actions = ["final_submit_without_explicit_user_approval"]
    safety_notes = [
        "Use only observed element_id values.",
        "Policy will validate risk, source, profile value matches, upload targets, and final submit gates.",
    ]

    if memory_context.saved_login_rejected:
        hints.append("saved_login_rejected_for_this_portal")
        blocked_actions.append("retry_rejected_default_password")
    if memory_context.create_account_available:
        hints.append("create_account_visible")
    if observation.page_type == "login":
        blocked_actions.append("stop_ready_to_submit_on_login")
    if _profile_path_value(profile_facts, "resume_path"):
        hints.append("resume_path_available")
    if _has_resume_upload(observation):
        hints.append("direct_resume_cv_upload_observed")
    if _has_profile_entry_choice_without_direct_upload(observation):
        hints.append("manual_entry_preferred_when_cv_parse_choice_has_no_direct_file_input")

    create_account_action = _find_create_account_action(observation)
    if memory_context.saved_login_rejected and create_account_action is not None:
        recommended_actions.append(
            {
                "action_type": "click",
                "element_id": create_account_action.element_id,
                "reason": "Saved login was rejected and create-account/register is visible.",
                "source": "memory",
            }
        )

    login_action = _find_login_action(observation)
    if observation.page_type == "login" and login_action is not None and _login_fields_have_useful_values(observation):
        recommended_actions.append(
            {
                "action_type": "click",
                "element_id": login_action.element_id,
                "reason": "Login fields are populated; Sign In is navigation/authentication, not final application submission.",
                "source": "page",
            }
        )

    manual_entry_action = _find_manual_entry_action(observation)
    if manual_entry_action is not None and _has_profile_entry_choice_without_direct_upload(observation):
        recommended_actions.append(
            {
                "action_type": "click",
                "element_id": manual_entry_action.element_id,
                "reason": "Manual entry is preferred over opaque CV parsing when no direct file input is observed.",
                "source": "page",
            }
        )

    return PlanningFrame(
        phase=phase,
        objective=_planning_objective(phase),
        strategies=strategies,
        hints=_dedupe_strings(hints),
        recommended_actions=recommended_actions,
        blocked_actions=_dedupe_strings(blocked_actions),
        safety_notes=safety_notes,
    )


def _planning_phase(
    observation: PageObservation,
    memory_context: ExternalApplyMemoryContext,
) -> str:
    if observation.page_type == "captcha":
        return "captcha"
    if memory_context.saved_login_rejected:
        return "account_recovery"
    if observation.page_type == "login":
        return "login"
    if observation.page_type == "resume_upload" or _has_resume_upload(observation):
        return "document_upload"
    if observation.page_type == "screening_questions":
        return "screening"
    if observation.page_type == "review":
        return "review"
    if observation.page_type == "final_submit":
        return "final_submit"
    if observation.page_type == "form":
        return "profile_form"
    return "unknown"


def _planning_objective(phase: str) -> str:
    objectives = {
        "login": "Complete safe login/account fields or choose an observed account path.",
        "account_recovery": "Recover from rejected saved portal credentials without retrying the rejected secret.",
        "profile_form": "Complete safe profile/application fields and prepare to advance.",
        "document_upload": "Upload only approved documents to matching observed upload fields.",
        "screening": "Answer safe screening questions from approved facts or ask the user.",
        "review": "Review current state and stop before final submission.",
        "final_submit": "Stop at the final submit gate.",
        "captcha": "Pause for human captcha handling.",
    }
    return objectives.get(phase, "Plan the safest next application action from the current observation.")


def _has_resume_upload(observation: PageObservation) -> bool:
    return any(_looks_like_resume_upload_field(field) for field in [*observation.fields, *observation.uploads])


def _looks_like_resume_upload_field(field: Any) -> bool:
    text = " ".join([field.label or "", field.nearby_text or ""]).lower()
    return (field.field_type or "").strip().lower() == "file" and bool(
        re.search(r"\b(resume|resum[eé]|cv|curriculum vitae)\b", text)
    )


def _has_profile_entry_choice_without_direct_upload(observation: PageObservation) -> bool:
    page_text = " ".join([observation.title, observation.visible_text]).lower()
    return (
        not _has_resume_upload(observation)
        and re.search(r"\b(best way to get your info|start your application|use my cv|type it in myself)\b", page_text) is not None
        and _find_manual_entry_action(observation) is not None
    )


def _find_manual_entry_action(observation: PageObservation) -> Any | None:
    for action in (*observation.buttons, *observation.links):
        if action.disabled:
            continue
        if re.search(r"\b(type it in myself|enter manually|manual entry|fill (?:it )?in myself|type manually)\b", action.label.lower()):
            return action
    return None


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


def _preapproved_account_route_action(
    observation: PageObservation,
    memory_context: ExternalApplyMemoryContext,
) -> ProposedAction | None:
    if not memory_context.saved_login_rejected:
        return None
    if not memory_context.create_account_available:
        return None
    action = _find_create_account_action(observation)
    if action is None or action.disabled:
        return None
    return ProposedAction(
        action_type="click",
        element_id=action.element_id,
        confidence=0.93,
        risk="medium",
        reason=(
            "The saved login was rejected for this external portal and a create-account path is visible, "
            "so Envoy should create a portal-specific account instead of asking for or retrying the same password."
        ),
        source="memory",
    )


def _coerce_login_terminal_action(state: ExternalApplyState) -> ExternalApplyState:
    observation = state.observation
    action = state.proposed_action
    if observation is None or action is None:
        return state
    if observation.page_type != "login" or action.action_type != "stop_ready_to_submit":
        return state

    login_action = _find_login_action(observation)
    if login_action is not None and _login_fields_have_useful_values(observation):
        coerced = ProposedAction(
            action_type="click",
            element_id=login_action.element_id,
            confidence=min(max(action.confidence, 0.9), 1.0),
            risk="medium",
            reason=(
                "The planner treated login as a final submit gate, but Sign In is portal authentication/navigation, "
                "not final application submission."
            ),
            source="page",
        )
        return state.model_copy(
            update={
                "proposed_action": coerced,
                "status": "running",
                "submit_ready": False,
                "pending_user_question": None,
                "pending_user_questions": [],
            }
        )

    missing = [
        field.label or field.element_id
        for field in observation.fields
        if field.required and not field.disabled and not _field_has_useful_value(field)
    ]
    question = UserQuestion(
        question="The login page is not ready to continue. Please provide the missing login details.",
        context="Required login fields are incomplete: " + ", ".join(missing) if missing else "The login action was not observed.",
        question_key=_question_key_for_prompt("login_not_ready", observation.url),
    )
    coerced = ProposedAction(
        action_type="ask_user",
        question=question.question,
        confidence=0.9,
        risk="medium",
        reason="stop_ready_to_submit is not valid on login pages.",
        source="page",
    )
    return state.model_copy(
        update={
            "proposed_action": coerced,
            "status": "paused_for_user",
            "submit_ready": False,
            "pending_user_question": question,
            "pending_user_questions": [question],
        }
    )


def _portal_host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _portal_identity(url: str, title: str = "") -> str:
    host = _portal_host(url)
    title_text = re.sub(r"\s+", " ", str(title or "")).strip()
    if title_text:
        return f"{host}|{title_text.lower()}" if host else title_text.lower()
    return host


def _portal_account_mode(profile_facts: dict[str, Any], portal_host: str) -> str | None:
    portal = _portal_account_record(profile_facts, portal_host)
    if not isinstance(portal, dict):
        return None
    mode = str(portal.get("account_mode") or portal.get("mode") or "").strip().lower()
    return mode or None


def _portal_account_record(profile_facts: dict[str, Any], portal_host: str) -> dict[str, Any] | None:
    external_accounts = profile_facts.get("external_accounts")
    if not isinstance(external_accounts, dict) or not portal_host:
        return None
    portals = external_accounts.get("portals")
    if not isinstance(portals, dict):
        return None
    portal = portals.get(portal_host) or portals.get(_registrable_portal_host(portal_host))
    if not isinstance(portal, dict):
        return None
    return portal


def _registrable_portal_host(portal_host: str) -> str:
    parts = [part for part in portal_host.lower().split(".") if part]
    if len(parts) <= 2:
        return portal_host.lower()
    return ".".join(parts[-2:])


def _portal_account_status(portal_account: dict[str, Any] | None) -> str | None:
    if not isinstance(portal_account, dict):
        return None
    status = str(
        portal_account.get("status")
        or portal_account.get("account_status")
        or portal_account.get("credential_status")
        or ""
    ).strip().lower()
    return status or None


def _portal_account_email(profile_facts: dict[str, Any], portal_account: dict[str, Any] | None) -> str | None:
    value = None
    if isinstance(portal_account, dict):
        value = portal_account.get("email") or portal_account.get("username")
    value = value or _profile_path_value(profile_facts, "external_accounts.default.email") or _profile_path_value(profile_facts, "email")
    text = str(value or "").strip()
    return text or None


def _portal_account_password(portal_account: dict[str, Any] | None) -> str | None:
    if not isinstance(portal_account, dict):
        return None
    value = portal_account.get("password") or portal_account.get("secret") or portal_account.get("credential")
    text = str(value or "").strip()
    return text or None


def _portal_credential_status(portal_account: dict[str, Any] | None, saved_login_rejected: bool) -> str | None:
    if saved_login_rejected:
        return "rejected"
    if not isinstance(portal_account, dict):
        return None
    status = str(portal_account.get("credential_status") or portal_account.get("password_status") or "").strip().lower()
    return status or None


def _recent_executor_failures(recent_actions: list[ActionTrace]) -> list[dict[str, object]]:
    failures: list[dict[str, object]] = []
    for trace in recent_actions[-6:]:
        result = trace.result
        if result is None or result.ok:
            continue
        action = trace.proposed_action
        field = _observed_field(trace.observation, action.element_id)
        failure: dict[str, object] = {
            "action_type": action.action_type,
            "element_id": action.element_id,
            "field_label": field.label if field is not None else "",
            "field_type": field.field_type if field is not None else "",
            "message": result.message,
            "errors": result.errors[:5],
        }
        if result.diagnostics:
            failure["diagnostics"] = _compact_executor_diagnostics(result.diagnostics)
        failures.append(failure)
    return failures


def _compact_executor_diagnostics(diagnostics: dict[str, object]) -> dict[str, object]:
    allowed_keys = {
        "field_label",
        "field_type",
        "control_tag",
        "control_role",
        "current_value_before",
        "current_value_after",
        "requested_value",
        "requested_value_normalized",
        "text_entry_capable",
        "allows_forgiving_fallback",
        "initial_options",
        "typed_options",
        "closed_state_options_before",
        "closed_state_options_after",
        "visible_errors_before",
        "visible_errors_after",
    }
    compact: dict[str, object] = {}
    for key, value in diagnostics.items():
        if key not in allowed_keys:
            continue
        if isinstance(value, list):
            compact[key] = value[:8]
        else:
            compact[key] = value
    return compact


def _find_create_account_action(observation: PageObservation) -> Any | None:
    for action in (*observation.buttons, *observation.links):
        if action.disabled:
            continue
        if _looks_like_create_account_label(action.label):
            return action
    return None


def _find_login_action(observation: PageObservation) -> Any | None:
    for action in (*observation.buttons, *observation.links):
        if action.disabled:
            continue
        if _looks_like_login_label(action.label):
            return action
    return None


def _login_fields_have_useful_values(observation: PageObservation) -> bool:
    required_login_fields = [
        field
        for field in observation.fields
        if field.required and field.visible and not field.disabled and _looks_like_login_field(field)
    ]
    return bool(required_login_fields) and all(_field_has_useful_value(field) for field in required_login_fields)


def _looks_like_login_field(field: Any) -> bool:
    text = " ".join([str(getattr(field, "label", "") or ""), str(getattr(field, "field_type", "") or "")]).lower()
    return bool(re.search(r"\b(user(?:name)?|email|e-mail|password|passcode|passphrase)\b", text))


def _has_recent_login_click(recent_actions: list[ActionTrace]) -> bool:
    return any(
        trace.proposed_action.action_type == "click"
        and _looks_like_login_label(_observed_action_label(trace.observation, trace.proposed_action.element_id) or "")
        and trace.result is not None
        and trace.result.ok
        for trace in recent_actions[-8:]
    )


def _saved_login_appears_rejected(
    observation: PageObservation,
    profile_facts: dict[str, Any],
    recent_actions: list[ActionTrace],
) -> bool:
    if observation.page_type != "login":
        return False
    if not _has_substantive_page_errors(observation.errors):
        return False
    if not _has_default_external_login(profile_facts):
        return False
    password_fill_index = _last_profile_password_fill_index(recent_actions)
    if password_fill_index is None:
        return False
    login_click_index = _last_login_click_index(recent_actions)
    return login_click_index is not None and login_click_index > password_fill_index


def _has_default_external_login(profile_facts: dict[str, Any]) -> bool:
    email = _profile_path_value(profile_facts, "external_accounts.default.email") or _profile_path_value(profile_facts, "email")
    password = _profile_path_value(profile_facts, "external_accounts.default.password") or _profile_path_value(profile_facts, "password")
    return bool(str(email or "").strip() and str(password or "").strip())


def _profile_path_value(profile_facts: dict[str, Any], path: str) -> Any:
    current: Any = profile_facts
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _last_profile_password_fill_index(recent_actions: list[ActionTrace]) -> int | None:
    for index in range(len(recent_actions) - 1, -1, -1):
        trace = recent_actions[index]
        action = trace.proposed_action
        if action.action_type != "fill_text" or action.source != "profile":
            continue
        field = _observed_field(trace.observation, action.element_id)
        label = field.label if field is not None else ""
        if re.search(r"\b(pass(word|code|phrase)?)\b", label.lower()):
            return index
    return None


def _last_login_click_index(recent_actions: list[ActionTrace]) -> int | None:
    for index in range(len(recent_actions) - 1, -1, -1):
        trace = recent_actions[index]
        if trace.proposed_action.action_type != "click":
            continue
        label = _observed_action_label(trace.observation, trace.proposed_action.element_id) or ""
        if _looks_like_login_label(label):
            return index
    return None


def _looks_like_create_account_label(label: str) -> bool:
    return bool(re.search(r"\b(create (?:a )?(?:new )?account|register|sign up|sign-up|join now)\b", label.lower()))


def _looks_like_login_label(label: str) -> bool:
    return bool(re.search(r"\b(sign in|sign-in|log in|login)\b", label.lower()))


def _approved_memory_with_recent_answers(
    approved_memory: list[dict[str, Any]],
    recent_actions: list[ActionTrace],
) -> list[dict[str, Any]]:
    """Promote successful user-sourced field answers into page-local memory.

    External ATS pages often re-render controls and assign new element ids after a
    pause. The browser action trace still has the old field label and user answer,
    so we make that answer available to the planner for semantically matching
    fields on the next observation.
    """

    memory = list(approved_memory)
    seen = {
        (
            str(item.get("label") or item.get("question") or "").strip().lower(),
            str(item.get("answer") or item.get("value") or "").strip().lower(),
        )
        for item in memory
    }
    for trace in recent_actions:
        result = trace.result
        if result is None or not result.ok:
            continue
        action = trace.proposed_action
        if action.source != "user":
            continue
        if action.action_type not in {"fill_text", "select_option", "set_checkbox", "set_radio", "upload_file", "ask_user"}:
            continue

        label = ""
        field_type = ""
        field = None
        if action.element_id:
            field = _observed_field(trace.observation, action.element_id)
            if field is not None:
                label = field.label
                field_type = field.field_type
        if not label:
            label = action.question or ""

        answer = action.value or result.value_after or ""
        if not label or not str(answer).strip():
            continue

        key = (label.strip().lower(), str(answer).strip().lower())
        if key in seen:
            continue
        seen.add(key)
        memory.append(
            {
                "label": label,
                "question": label,
                "answer": str(answer),
                "value": str(answer),
                "field_type": field_type,
                "source": "user",
                **(_question_memory_metadata(field, trace.observation) if field is not None else {}),
            }
        )
    return memory


def _question_memory_metadata(field: Any, observation: PageObservation) -> dict[str, Any]:
    options = [str(option).strip() for option in getattr(field, "options", []) if str(option).strip()]
    label = str(getattr(field, "label", "") or "").strip()
    nearby_text = str(getattr(field, "nearby_text", "") or "").strip()
    return {
        "portal_host": _portal_host(observation.url),
        "question_fingerprint": _question_fingerprint(label, nearby_text, options),
        "option_signature": _option_signature(options),
        "options": options[:12],
    }


def _question_fingerprint(label: str, nearby_text: str, options: list[str]) -> str:
    basis = "\n".join(
        [
            _normalize_memory_text(label),
            _normalize_memory_text(nearby_text)[:300],
            _option_signature(options),
        ]
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _option_signature(options: list[str]) -> str:
    normalized = [_normalize_memory_text(option) for option in options if _normalize_memory_text(option)]
    return "|".join(normalized[:20])


def _normalize_memory_text(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(value).lower())
    return re.sub(r"\s+", " ", text).strip()


async def apply_external_user_answers(
    tool_client: ToolClient,
    *,
    session_key: str,
    external_state: ExternalApplyState,
    answers_by_element_id: dict[str, str],
    answers_by_question_key: dict[str, str] | None = None,
    question_cache: SqliteQuestionCacheRepository | None = None,
    execute_fn: ExecuteFn = execute_external_apply_action,
) -> ExternalApplyState:
    questions = external_state.pending_user_questions or _pending_question_list(external_state.pending_user_question)
    if not questions:
        return external_state

    current_state = external_state
    answers_by_question_key = answers_by_question_key or {}
    unanswered: list[UserQuestion] = []
    for question in questions:
        target_question = _bind_user_question_to_observation(question, current_state.observation)
        target_id = target_question.target_element_id
        if not target_id:
            question_key = target_question.question_key or _question_key_for_prompt(target_question.question, target_question.context)
            answer = answers_by_question_key.get(question_key, "").strip()
            if answer and _truthy_answer(answer):
                if _looks_like_generic_consent_prompt(target_question.question, target_question.context):
                    current_state = _record_generic_prompt_ack(
                        current_state,
                        target_question,
                        answer,
                        fallback_reason="User approved a generic external-apply consent prompt.",
                        result_message="User approved the generic external-apply consent prompt.",
                    )
                    continue
                if _looks_like_generic_review_prompt(target_question.question, target_question.context):
                    current_state = _record_generic_prompt_ack(
                        current_state,
                        target_question,
                        answer,
                        fallback_reason="User reviewed the external page and chose to continue the harness.",
                        result_message="User confirmed the external page was reviewed and the harness may continue.",
                    )
                    continue
            unanswered.append(target_question)
            continue
        question_key = target_question.question_key or _question_key_for_prompt(target_question.question, target_question.context)
        answer = answers_by_element_id.get(target_id, "").strip() or answers_by_question_key.get(question_key, "").strip()
        if not answer:
            unanswered.append(target_question)
            continue
        current_state = await apply_external_user_answer(
            tool_client,
            session_key=session_key,
            external_state=current_state.model_copy(update={"pending_user_question": target_question}),
            answer=answer,
            question_cache=question_cache,
            execute_fn=execute_fn,
        )
        if current_state.status == "failed":
            return current_state

    if unanswered:
        return current_state.model_copy(
            update={
                "status": "paused_for_user",
                "pending_user_question": unanswered[0],
                "pending_user_questions": unanswered,
            }
        )

    return current_state.model_copy(update={"pending_user_question": None, "pending_user_questions": []})


def realign_external_state_to_observation(
    external_state: ExternalApplyState,
    observation: PageObservation,
) -> ExternalApplyState:
    questions = external_state.pending_user_questions or _pending_question_list(external_state.pending_user_question)
    rebound_questions = [
        _realign_user_question_to_observation(question, external_state.observation, observation)
        for question in questions
    ]
    rebound_questions = [question for question in rebound_questions if question is not None]
    return external_state.model_copy(
        update={
            "observation": observation,
            "current_url": observation.url or external_state.current_url,
            "page_type": observation.page_type,
            "pending_user_question": rebound_questions[0] if rebound_questions else None,
            "pending_user_questions": rebound_questions,
        }
    )


async def apply_external_user_answer(
    tool_client: ToolClient,
    *,
    session_key: str,
    external_state: ExternalApplyState,
    answer: str,
    question_cache: SqliteQuestionCacheRepository | None = None,
    execute_fn: ExecuteFn = execute_external_apply_action,
) -> ExternalApplyState:
    """Apply an explicit user answer to the paused external page.

    This is used for questions like privacy consent where the planner correctly
    paused for human confirmation. The answer is treated as user-sourced and
    still recorded as an auditable action trace.
    """

    observation = external_state.observation
    target_id = external_state.pending_user_question.target_element_id if external_state.pending_user_question else None
    if observation is None or not target_id:
        return external_state.model_copy(
            update={
                "status": "paused_for_user",
                "error": "No paused external question target was available.",
                "risk_flags": [*external_state.risk_flags, "missing_user_answer_target"],
            }
        )

    target_field = next((field for field in observation.fields if field.element_id == target_id), None)
    target_button = None
    if target_field is None:
        target_button = next(
            (btn for btn in (*observation.buttons, *observation.links) if btn.element_id == target_id),
            None,
        )
    if target_field is None and target_button is None:
        return external_state.model_copy(
            update={
                "status": "failed",
                "error": f"User-approved target element was not present: {target_id}",
                "risk_flags": [*external_state.risk_flags, "missing_user_answer_target"],
            }
        )

    if target_field is not None:
        action = _action_from_user_answer(target_field.element_id, target_field.field_type, answer)
    else:
        action = _action_from_user_answer_for_button(target_button.element_id, answer)
        if action is None:
            return external_state.model_copy(
                update={
                    "status": "running",
                    "pending_user_question": None,
                    "pending_user_questions": [],
                    "proposed_action": None,
                    "risk_flags": [*external_state.risk_flags, "user_declined_button"],
                }
            )
    result = await execute_fn(tool_client, session_key, action)
    if result.ok and target_field is not None:
        await _save_external_user_answer_to_cache(target_field, answer, question_cache)
    trace = ActionTrace(
        observation=observation,
        proposed_action=action,
        policy_decision="allowed",
        result=result,
    )
    return external_state.model_copy(
        update={
            "completed_actions": [*external_state.completed_actions, trace],
            "last_action_result": result,
            "current_url": result.new_url or external_state.current_url,
            "status": "running" if result.ok else "failed",
            "pending_user_question": None if result.ok else external_state.pending_user_question,
            "pending_user_questions": [] if result.ok else external_state.pending_user_questions,
            "error": None if result.ok else result.message,
            "risk_flags": result.errors,
        }
    )


async def _save_external_user_answer_to_cache(
    field: Any,
    answer: str,
    question_cache: SqliteQuestionCacheRepository | None,
) -> None:
    if question_cache is None:
        return
    label = str(getattr(field, "label", "") or "").strip()
    clean_answer = str(answer or "").strip()
    if not _is_cacheable_external_answer_field(field):
        return
    if not label or not clean_answer:
        return
    await question_cache.save(
        label,
        clean_answer,
        field_type=str(getattr(field, "field_type", "") or ""),
        source="human_external",
    )


def _is_cacheable_external_answer_field(field: Any) -> bool:
    label = str(getattr(field, "label", "") or "").lower()
    field_type = str(getattr(field, "field_type", "") or "").lower()
    text = " ".join([label, field_type])
    return not bool(
        re.search(
            r"\b(pass(word|code|phrase)?|one[-\s]?time|otp|verification code|security code|captcha|secret)\b",
            text,
        )
    )


def _status_for_policy_pause(policy: PolicyDecision, action: ProposedAction) -> HarnessStatus:
    if policy.decision == "rejected":
        return "failed"
    if policy.pause_reason == "final_submit" or action.action_type == "stop_ready_to_submit":
        return "ready_to_submit"
    if policy.pause_reason == "needs_approval":
        return "paused_for_approval"
    return "paused_for_user"


def _user_question_for_policy(
    policy: PolicyDecision,
    action: ProposedAction,
    observation: PageObservation | None = None,
) -> UserQuestion | None:
    if policy.decision == "rejected" or policy.pause_reason == "final_submit":
        return None
    if observation is not None and action.element_id:
        field = _observed_field(observation, action.element_id)
        if field is not None:
            return UserQuestion(
                question=_question_for_field(field),
                context=policy.reason,
                suggested_answers=field.options,
                target_element_id=field.element_id,
                question_key=_question_key_for_action(action),
            )
    return UserQuestion(
        question=action.question or "Envoy needs your input or approval before continuing.",
        context=policy.reason,
        target_element_id=action.element_id,
        question_key=_question_key_for_action(action),
    )


def _user_question_for_action(action: ProposedAction, observation: PageObservation | None = None) -> UserQuestion | None:
    if action.action_type != "ask_user":
        return None

    field = _observed_field(observation, action.element_id) if observation is not None else None
    if field is None and observation is not None:
        field = _field_matching_user_question(action.question or "", action.reason or "", observation)
    target_element_id = field.element_id if field is not None else action.element_id
    return UserQuestion(
        question=action.question or (_question_for_field(field) if field is not None else "Envoy needs your input before continuing."),
        context=action.reason,
        suggested_answers=field.options if field is not None else [],
        target_element_id=target_element_id,
        question_key=_question_key_for_action(action),
    )


def _user_questions_for_action(action: ProposedAction, observation: PageObservation | None = None) -> list[UserQuestion]:
    question = _user_question_for_action(action, observation)
    if question is None:
        return []
    if observation is None:
        return [question]
    expanded = _field_questions_for_compound_user_prompt(action, observation)
    return expanded or [question]


def _field_questions_for_compound_user_prompt(
    action: ProposedAction,
    observation: PageObservation,
) -> list[UserQuestion]:
    if action.action_type != "ask_user" or not action.question:
        return []
    if _looks_like_generic_consent_prompt(action.question, action.reason) or _looks_like_generic_review_prompt(action.question, action.reason):
        return []

    question_tokens = _question_match_tokens(" ".join([action.question, action.reason]))
    question_text = _normalize_memory_text(" ".join([action.question, action.reason]))
    matches: list[tuple[float, Any]] = []
    for field in observation.fields:
        if getattr(field, "disabled", False) or not getattr(field, "visible", True):
            continue
        if _field_has_useful_value(field):
            continue
        score = _field_question_match_score(field, question_tokens, question_text)
        if score >= 0.65:
            matches.append((score, field))

    if len(matches) <= 1:
        return []

    fields_by_id = {field.element_id: field for _, field in sorted(matches, key=lambda item: _field_sort_key(observation, item[1]))}
    return [
        UserQuestion(
            question=_question_for_field(field),
            context=action.reason,
            suggested_answers=field.options,
            target_element_id=field.element_id,
            question_key=_question_key_for_prompt(f"{field.element_id}|compound|{action.question}", action.reason),
        )
        for field in fields_by_id.values()
    ]


def _field_sort_key(observation: PageObservation, target_field: Any) -> int:
    for index, field in enumerate(observation.fields):
        if field.element_id == target_field.element_id:
            return index
    return len(observation.fields)


def _user_questions_for_pause(
    observation: PageObservation,
    current_action: ProposedAction,
    remaining_actions: list[ProposedAction],
    policy: PolicyDecision,
) -> list[UserQuestion]:
    questions: list[UserQuestion] = []
    first_questions = _user_questions_for_action(current_action, observation)
    if first_questions:
        questions.extend(first_questions)
    else:
        first_question = _user_question_for_policy(policy, current_action, observation)
        if first_question is not None:
            questions.append(first_question)

    seen_targets = {question.target_element_id for question in questions if question.target_element_id}
    for action in remaining_actions:
        if action.action_type != "ask_user":
            continue
        for question in _user_questions_for_action(action, observation):
            if question.target_element_id and question.target_element_id in seen_targets:
                continue
            questions.append(question)
            if question.target_element_id:
                seen_targets.add(question.target_element_id)
    return questions


def _required_field_questions_before_click(
    observation: PageObservation,
    action: ProposedAction,
    remaining_actions: list[ProposedAction],
) -> list[UserQuestion]:
    if action.action_type != "click":
        return []
    if _click_allows_missing_required_fields(observation, action):
        return []

    planned_targets = {
        candidate.element_id
        for candidate in remaining_actions
        if candidate.element_id and candidate.action_type in {"fill_text", "select_option", "set_checkbox", "set_radio", "upload_file", "ask_user"}
    }
    missing_fields = [
        field
        for field in observation.fields
        if field.required
        and field.visible
        and not field.disabled
        and field.element_id not in planned_targets
        and not _field_has_useful_value(field)
    ]
    if not missing_fields:
        return []

    context_lines = [
        "Required fields are still incomplete on the current page, so Envoy should not continue yet.",
    ]
    if observation.errors:
        context_lines.extend(["Current page messages:", *observation.errors[:6]])
    context = "\n".join(context_lines)
    return [
        UserQuestion(
            question=_question_for_field(field),
            context=context,
            suggested_answers=field.options,
            target_element_id=field.element_id,
            question_key=_question_key_for_prompt(f"{field.element_id}|required", context),
        )
        for field in missing_fields
    ]


def _click_allows_missing_required_fields(
    observation: PageObservation,
    action: ProposedAction,
) -> bool:
    label = (_observed_action_label(observation, action.element_id) or "").strip()
    if not label:
        return False
    if observation.page_type == "login" and _looks_like_create_account_label(label):
        return True
    return False


def _pending_question_list(question: UserQuestion | None) -> list[UserQuestion]:
    return [question] if question is not None else []


def _most_recent_matching_click_trace(
    completed_actions: list[ActionTrace],
    element_id: str,
) -> ActionTrace | None:
    for trace in reversed(completed_actions):
        if trace.proposed_action.action_type != "click":
            continue
        if trace.proposed_action.element_id != element_id:
            continue
        return trace
    return None


def _stale_repeated_click_question(
    observation: PageObservation,
    action: ProposedAction,
    completed_actions: list[ActionTrace],
) -> UserQuestion | None:
    if action.action_type != "click" or not action.element_id or not completed_actions:
        return None
    previous = _most_recent_matching_click_trace(completed_actions, action.element_id)
    if previous is None:
        return None
    previous_result = previous.result
    if previous_result is None or not previous_result.ok or previous_result.navigated:
        return None
    if not _same_page_shape(observation, previous.observation):
        return None

    button_label = _observed_action_label(observation, action.element_id) or "that button"
    context = (
        f"The page stayed on the same step after clicking {button_label}. "
        "Please review any highlighted errors or missing fields, then continue when the page is ready."
    )
    if observation.errors:
        context = "\n".join([context, "Current page messages:", *observation.errors[:6]])
    return UserQuestion(
        question=f"The page did not advance after clicking {button_label}. Review the page and continue when it is ready.",
        context=context,
        question_key=_question_key_for_action(action),
    )


async def _observe_delayed_transition_after_repeated_click(
    observation: PageObservation,
    action: ProposedAction,
    completed_actions: list[ActionTrace],
    tool_client: ToolClient,
    session_key: str,
    observe_fn: ObserveFn,
    sleep_fn: SleepFn,
) -> PageObservation | None:
    if action.action_type != "click" or not action.element_id or not completed_actions:
        return None
    previous = _most_recent_matching_click_trace(completed_actions, action.element_id)
    if previous is None:
        return None
    previous_result = previous.result
    if previous_result is None or not previous_result.ok or previous_result.navigated:
        return None
    if not _same_page_shape(observation, previous.observation):
        return None
    if not _looks_like_slow_transition_click(observation, action.element_id):
        return None
    if _has_substantive_page_errors(observation.errors) or _has_substantive_page_errors(previous_result.errors):
        return None

    for delay_seconds in (1.0, 2.0, 4.0):
        await sleep_fn(delay_seconds)
        next_observation = await observe_fn(tool_client, session_key)
        if not _same_page_shape(next_observation, observation):
            return next_observation
    return None


def _observed_field(observation: PageObservation | None, element_id: str | None) -> Any | None:
    if observation is None or not element_id:
        return None
    return next((field for field in observation.fields if field.element_id == element_id), None)


def _realign_user_question_to_observation(
    question: UserQuestion,
    previous_observation: PageObservation | None,
    current_observation: PageObservation,
) -> UserQuestion | None:
    if question.target_element_id is None:
        return _bind_user_question_to_observation(question, current_observation)

    direct_match = _observed_field(current_observation, question.target_element_id)
    if direct_match is not None:
        return question.model_copy(update={"suggested_answers": direct_match.options})

    previous_field = _observed_field(previous_observation, question.target_element_id)
    target_label = (previous_field.label if previous_field is not None else _field_label_from_question(question.question)) or ""
    normalized_target = _normalize_field_label(target_label)
    if not normalized_target:
        return None

    for field in current_observation.fields:
        if _normalize_field_label(field.label) != normalized_target:
            continue
        return question.model_copy(
            update={
                "target_element_id": field.element_id,
                "suggested_answers": field.options,
            }
        )
    return None


def _bind_user_question_to_observation(
    question: UserQuestion,
    observation: PageObservation | None,
) -> UserQuestion:
    if observation is None or question.target_element_id is not None:
        return question
    field = _field_matching_user_question(question.question, question.context, observation)
    if field is None:
        return question
    return question.model_copy(
        update={
            "target_element_id": field.element_id,
            "suggested_answers": field.options,
        }
    )


def _field_matching_user_question(
    question: str,
    context: str,
    observation: PageObservation,
) -> Any | None:
    if _looks_like_generic_review_prompt(question, context):
        return None

    question_tokens = _question_match_tokens(" ".join([question, context]))
    if not question_tokens:
        return None

    best_field = None
    best_score = 0.0
    second_best_score = 0.0
    question_text = _normalize_memory_text(" ".join([question, context]))
    for field in observation.fields:
        if getattr(field, "disabled", False) or not getattr(field, "visible", True):
            continue
        score = _field_question_match_score(field, question_tokens, question_text)

        if score > best_score:
            second_best_score = best_score
            best_score = score
            best_field = field
        elif score > second_best_score:
            second_best_score = score

    if best_field is None or best_score < 0.65:
        return None
    if second_best_score >= 0.65 and best_score - second_best_score < 0.15:
        return None
    return best_field


def _field_question_match_score(field: Any, question_tokens: set[str], question_text: str) -> float:
    label = str(getattr(field, "label", "") or "")
    nearby_text = str(getattr(field, "nearby_text", "") or "")
    field_tokens = _question_match_tokens(" ".join([label, nearby_text]))
    if not field_tokens:
        return 0.0

    label_text = _normalize_memory_text(label)
    overlap = len(question_tokens & field_tokens)
    score = overlap / max(len(field_tokens), 1)
    if label_text and (label_text in question_text or question_text in label_text):
        score = max(score, 1.0)

    field_type = str(getattr(field, "field_type", "") or "").lower()
    option_tokens = {_normalize_memory_text(option) for option in getattr(field, "options", [])}
    if field_type in {"radio", "select"} and {"yes", "no"}.issubset(option_tokens):
        score += 0.1
    if getattr(field, "required", False):
        score += 0.05
    return score


def _question_match_tokens(value: str) -> set[str]:
    stop_words = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "in",
        "is",
        "it",
        "no",
        "of",
        "or",
        "please",
        "question",
        "required",
        "select",
        "should",
        "the",
        "there",
        "this",
        "to",
        "user",
        "what",
        "whether",
        "yes",
        "you",
        "your",
    }
    return {
        _question_match_token(token)
        for token in _normalize_memory_text(value).split()
        if (len(token) > 1 or token.isdigit()) and token not in stop_words
    }


def _question_match_token(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _field_label_from_question(question: str) -> str:
    if ":" not in question:
        return ""
    _, remainder = question.split(":", 1)
    return remainder.strip().rstrip("?")


def _normalize_field_label(label: str) -> str:
    return re.sub(r"\s+", " ", (label or "").strip().rstrip("*")).lower()


def _question_for_field(field: Any) -> str:
    label = (field.label or "this field").strip()
    field_type = (field.field_type or "").strip().lower()
    if field_type == "checkbox":
        return f"Should I tick: {label}?"
    if field_type in {"select", "radio"}:
        return f"What should I select for: {label}?"
    if field_type == "file":
        return f"What file should I upload for: {label}?"
    return f"How should I answer: {label}?"


def _observed_action_label(observation: PageObservation | None, element_id: str | None) -> str | None:
    if observation is None or not element_id:
        return None
    action = next(
        (candidate for candidate in (*observation.buttons, *observation.links) if candidate.element_id == element_id),
        None,
    )
    return action.label if action is not None else None


def _same_page_shape(current: PageObservation, previous: PageObservation) -> bool:
    return (
        current.url == previous.url
        and current.page_type == previous.page_type
        and _field_shape(current.fields) == _field_shape(previous.fields)
        and _action_shape(current.buttons) == _action_shape(previous.buttons)
        and _action_shape(current.links) == _action_shape(previous.links)
    )


def _field_shape(fields: list[Any]) -> list[tuple[str, str, bool, bool]]:
    return [
        (
            (field.label or "").strip().lower(),
            (field.field_type or "").strip().lower(),
            bool(field.required),
            bool((field.current_value or "").strip()),
        )
        for field in fields
    ]


def _looks_like_slow_transition_click(observation: PageObservation, element_id: str) -> bool:
    label = (_observed_action_label(observation, element_id) or "").strip().lower()
    return bool(re.search(r"\b(create account|sign in|sign-in|log in|login|save and continue|continue|next)\b", label))


def _has_substantive_page_errors(errors: list[str]) -> bool:
    progress_only = re.compile(r"^(current step \d+ of \d+|step \d+ of \d+)$", re.IGNORECASE)
    return any(error.strip() and not progress_only.fullmatch(error.strip()) for error in errors)


def _action_shape(actions: list[Any]) -> list[tuple[str, str, bool]]:
    return [
        (
            (action.label or "").strip().lower(),
            (action.kind or "").strip().lower(),
            bool(action.disabled),
        )
        for action in actions
    ]


def _question_key_for_action(action: ProposedAction) -> str:
    seed = "|".join([action.element_id or "", action.question or "", action.reason or ""])
    return f"question-indirect_{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:12]}"


def _question_key_for_prompt(question: str, context: str = "") -> str:
    seed = f"|{question}|{context}"
    return f"question-indirect_{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:12]}"


def _looks_like_generic_consent_prompt(question: str, context: str = "") -> bool:
    text = " ".join([question, context]).lower()
    return any(
        term in text
        for term in (
            "consent",
            "agree",
            "approval",
            "approve",
            "acknowledge",
            "accept",
            "terms and conditions",
            "terms & conditions",
            "privacy",
        )
    )


def _looks_like_generic_review_prompt(question: str, context: str = "") -> bool:
    text = " ".join([question, context]).lower()
    return any(
        phrase in text
        for phrase in (
            "review the page and continue",
            "continue when the page is ready",
            "page did not advance after clicking",
            "page stayed on the same step after clicking",
            "highlighted errors or missing fields",
        )
    )


def _approved_generic_consent_keys(recent_actions: list[ActionTrace]) -> set[str]:
    approved: set[str] = set()
    for trace in recent_actions:
        action = trace.proposed_action
        result = trace.result
        if action.action_type != "ask_user" or action.element_id:
            continue
        if result is None or not result.ok:
            continue
        if not _looks_like_generic_consent_prompt(action.question or "", action.reason):
            continue
        approved.add(_question_key_for_action(action))
    return approved


def _preapproved_generic_consent_action(
    observation: PageObservation,
    recent_actions: list[ActionTrace],
    profile_facts: dict[str, Any],
) -> ProposedAction | None:
    if not _approved_generic_consent_keys(recent_actions):
        return None
    for field in observation.fields:
        if not should_default_check_consent_field(observation, field, profile_facts):
            continue
        if field.current_value:
            continue
        return ProposedAction(
            action_type="set_checkbox",
            element_id=field.element_id,
            value="true",
            confidence=1.0,
            risk="low",
            reason="User previously approved the required consent prompt and the checkbox is now observable.",
            source="user",
        )
    return None


def _apply_default_safe_action(state: ExternalApplyState, profile_facts: dict[str, Any]) -> ExternalApplyState:
    observation = state.observation
    action = state.proposed_action
    if observation is None or action is None or not action.element_id:
        return state

    target_field = next((field for field in observation.fields if field.element_id == action.element_id), None)
    if target_field is None or not should_default_check_consent_field(observation, target_field, profile_facts):
        return state

    if action.action_type not in {"ask_user", "set_checkbox"}:
        return state

    default_action = ProposedAction(
        action_type="set_checkbox",
        element_id=target_field.element_id,
        value="true",
        confidence=1.0,
        risk="low",
        reason="Standard required application privacy/data handling consent is configured as a default-safe action.",
        source="user",
    )
    return state.model_copy(
        update={
            "proposed_action": default_action,
            "status": "running",
            "pending_user_question": None,
            "pending_user_questions": [],
        }
    )


def _coerce_noncritical_select_option(state: ExternalApplyState) -> ExternalApplyState:
    observation = state.observation
    action = state.proposed_action
    if observation is None or action is None or action.action_type != "select_option" or not action.element_id:
        return state

    target_field = next((field for field in observation.fields if field.element_id == action.element_id), None)
    if target_field is None or not _is_noncritical_source_select_field(target_field):
        return state

    options = _usable_select_options(target_field.options)
    if not options:
        return state

    if action.value and any(_normalize_option_text(option) == _normalize_option_text(action.value) for option in options):
        return state

    fallback_value = options[0]
    fallback_action = action.model_copy(
        update={
            "value": fallback_value,
            "reason": (
                f"{action.reason} The configured source value was not available on this page, "
                f"so Envoy selected the first safe available option: {fallback_value}."
            ).strip(),
        }
    )
    return state.model_copy(update={"proposed_action": fallback_action})


def _is_noncritical_source_select_field(field: Any) -> bool:
    if (field.field_type or "").strip().lower() != "select":
        return False
    label = " ".join([field.label or "", field.nearby_text or ""]).lower()
    return bool(re.search(r"\b(how did you hear|heard about|source)\b", label))


def _usable_select_options(options: list[str]) -> list[str]:
    usable: list[str] = []
    for option in options:
        text = option.strip()
        if not text:
            continue
        lowered = _normalize_option_text(text)
        if lowered in {"select", "select one", "choose", "choose one", "please select", "please choose"}:
            continue
        if lowered.startswith("--") and lowered.endswith("--"):
            continue
        usable.append(text)
    return usable


def _normalize_option_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _field_has_useful_value(field: Any) -> bool:
    if getattr(field, "invalid", False):
        return False
    value = (field.current_value or "").strip()
    if not value:
        return False
    if (field.field_type or "").strip().lower() == "select":
        if _normalize_option_text(value) in {"select", "select one", "choose", "choose one", "please select", "please choose"}:
            return False
    return True


def _record_generic_prompt_ack(
    state: ExternalApplyState,
    question: UserQuestion,
    answer: str,
    *,
    fallback_reason: str,
    result_message: str,
) -> ExternalApplyState:
    action = ProposedAction(
        action_type="ask_user",
        question=question.question,
        confidence=1.0,
        risk="medium",
        reason=question.context or fallback_reason,
        source="user",
    )
    result = ActionResult(
        ok=True,
        action_type="ask_user",
        message=result_message,
        value_after=answer,
        new_url=state.current_url,
    )
    trace = ActionTrace(
        observation=state.observation or PageObservation(url=state.current_url or "", page_type=state.page_type),
        proposed_action=action,
        policy_decision="allowed",
        result=result,
    )
    return state.model_copy(
        update={
            "completed_actions": [*state.completed_actions, trace],
            "pending_user_question": None,
            "pending_user_questions": [],
            "status": "running",
            "error": None,
        }
    )


def _action_from_user_answer(element_id: str, field_type: str, answer: str) -> ProposedAction:
    if field_type == "checkbox":
        action_type = "set_checkbox"
        value = "true" if _truthy_answer(answer) else "false"
    elif field_type == "radio":
        action_type = "set_radio"
        value = answer
    elif field_type == "select":
        action_type = "select_option"
        value = answer
    elif field_type == "file":
        action_type = "upload_file"
        value = answer
    else:
        action_type = "fill_text"
        value = answer

    return ProposedAction(
        action_type=action_type,  # type: ignore[arg-type]
        element_id=element_id,
        value=value,
        confidence=1.0,
        risk="medium",
        reason="User explicitly answered the paused external-apply question.",
        source="user",
    )


def _action_from_user_answer_for_button(element_id: str, answer: str) -> ProposedAction | None:
    if not _truthy_answer(answer):
        return None
    return ProposedAction(
        action_type="click",
        element_id=element_id,
        confidence=1.0,
        risk="medium",
        reason="User approved clicking the paused external-apply button.",
        source="user",
    )


def _truthy_answer(answer: str) -> bool:
    return answer.strip().lower() in {"1", "true", "yes", "y", "checked", "consent", "confirmed", "approve", "approved"}
