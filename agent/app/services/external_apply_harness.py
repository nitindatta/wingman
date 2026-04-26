"""Custom harness shell for external apply pages."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from typing import Any

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
    ExternalApplyState,
    HarnessStatus,
    PageObservation,
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


async def plan_external_apply_step(
    settings: Settings,
    tool_client: ToolClient,
    *,
    session_key: str,
    application_id: str,
    profile_facts: dict[str, Any],
    approved_memory: list[dict[str, Any]] | None = None,
    recent_actions: list[ActionTrace] | None = None,
    observe_fn: ObserveFn = observe_external_apply,
    planner_fn: PlanFn = propose_external_apply_action,
) -> ExternalApplyState:
    """Observe a page and propose one next action without executing it."""

    memory = approved_memory or []
    traces = recent_actions or []
    observation = await observe_fn(tool_client, session_key)
    proposed_action = await planner_fn(
        settings,
        observation=observation,
        profile_facts=profile_facts,
        approved_memory=memory,
        recent_actions=traces,
    )

    return ExternalApplyState(
        application_id=application_id,
        current_url=observation.url,
        page_type=observation.page_type,
        observation=observation,
        proposed_action=proposed_action,
        completed_actions=traces,
        status=_status_for_proposed_action(proposed_action),
        submit_ready=proposed_action.action_type == "stop_ready_to_submit",
        pending_user_question=_user_question_for_action(proposed_action, observation),
        pending_user_questions=_pending_question_list(_user_question_for_action(proposed_action, observation)),
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
    observe_fn: ObserveFn = observe_external_apply,
    planner_fn: PlanFn = propose_external_apply_action,
    batch_planner_fn: BatchPlanFn | None = None,
    policy_fn: PolicyFn = validate_external_apply_action,
    execute_fn: ExecuteFn = execute_external_apply_action,
) -> ExternalApplyState:
    """Run one observe-plan-policy-execute step or one safe page-level batch.

    The action is executed only when the deterministic policy returns
    decision="allowed". Paused/rejected actions are returned as state for the
    portal or workflow to handle.
    """

    memory = approved_memory or []
    traces = list(recent_actions or [])
    observation = await observe_fn(tool_client, session_key)
    _emit("observe", f"observe: {observation.page_type} @ {observation.url[:70]}", {
        "url": observation.url,
        "page_type": observation.page_type,
        "fields_count": len(observation.fields),
        "buttons_count": len(observation.buttons),
        "fields": [{"id": f.element_id, "label": f.label, "type": f.field_type} for f in observation.fields[:10]],
        "buttons": [{"id": b.element_id, "label": b.label} for b in observation.buttons[:6]],
        "visible_text": (observation.visible_text or "")[:200],
    })
    preapproved_consent_action = _preapproved_generic_consent_action(observation, traces, profile_facts)
    if preapproved_consent_action is not None:
        actions = [preapproved_consent_action]
    else:
        effective_batch_planner = batch_planner_fn
        if effective_batch_planner is None and planner_fn is propose_external_apply_action:
            effective_batch_planner = propose_external_apply_actions

        if effective_batch_planner is not None:
            actions = await effective_batch_planner(
                settings,
                observation=observation,
                profile_facts=profile_facts,
                approved_memory=memory,
                recent_actions=traces,
            )
        else:
            actions = [
                await planner_fn(
                    settings,
                    observation=observation,
                    profile_facts=profile_facts,
                    approved_memory=memory,
                    recent_actions=traces,
                )
            ]

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

    completed_actions = traces
    current_url = observation.url
    last_result: ActionResult | None = None
    last_state: ExternalApplyState | None = None
    mutated_current_page = False

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
        if action.action_type == "click" and mutated_current_page and last_state is not None:
            return last_state

        planned = ExternalApplyState(
            application_id=application_id,
            current_url=current_url,
            page_type=observation.page_type,
            observation=observation,
            proposed_action=action,
            completed_actions=completed_actions,
            status=_status_for_proposed_action(action),
            submit_ready=action.action_type == "stop_ready_to_submit",
            pending_user_question=_user_question_for_action(action, observation),
            pending_user_questions=_pending_question_list(_user_question_for_action(action, observation)),
            last_action_result=last_result,
        )
        planned = _apply_default_safe_action(planned, profile_facts)
        if planned.proposed_action is None:
            return planned
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
        current_url = result.new_url or current_url
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

    if last_state is not None:
        return last_state

    first_action = actions[0]
    return ExternalApplyState(
        application_id=application_id,
        current_url=current_url,
        page_type=observation.page_type,
        observation=observation,
        proposed_action=first_action,
        completed_actions=completed_actions,
        status=_status_for_proposed_action(first_action),
        submit_ready=first_action.action_type == "stop_ready_to_submit",
        pending_user_question=_user_question_for_action(first_action, observation),
        pending_user_questions=_pending_question_list(_user_question_for_action(first_action, observation)),
    )


async def apply_external_user_answers(
    tool_client: ToolClient,
    *,
    session_key: str,
    external_state: ExternalApplyState,
    answers_by_element_id: dict[str, str],
    answers_by_question_key: dict[str, str] | None = None,
    execute_fn: ExecuteFn = execute_external_apply_action,
) -> ExternalApplyState:
    questions = external_state.pending_user_questions or _pending_question_list(external_state.pending_user_question)
    if not questions:
        return external_state

    current_state = external_state
    answers_by_question_key = answers_by_question_key or {}
    unanswered: list[UserQuestion] = []
    for question in questions:
        target_id = question.target_element_id
        if not target_id:
            question_key = question.question_key or _question_key_for_prompt(question.question, question.context)
            answer = answers_by_question_key.get(question_key, "").strip()
            if answer and _truthy_answer(answer):
                if _looks_like_generic_consent_prompt(question.question, question.context):
                    current_state = _record_generic_prompt_ack(
                        current_state,
                        question,
                        answer,
                        fallback_reason="User approved a generic external-apply consent prompt.",
                        result_message="User approved the generic external-apply consent prompt.",
                    )
                    continue
                if _looks_like_generic_review_prompt(question.question, question.context):
                    current_state = _record_generic_prompt_ack(
                        current_state,
                        question,
                        answer,
                        fallback_reason="User reviewed the external page and chose to continue the harness.",
                        result_message="User confirmed the external page was reviewed and the harness may continue.",
                    )
                    continue
            unanswered.append(question)
            continue
        answer = answers_by_element_id.get(target_id, "").strip()
        if not answer:
            unanswered.append(question)
            continue
        current_state = await apply_external_user_answer(
            tool_client,
            session_key=session_key,
            external_state=current_state.model_copy(update={"pending_user_question": question}),
            answer=answer,
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


async def apply_external_user_answer(
    tool_client: ToolClient,
    *,
    session_key: str,
    external_state: ExternalApplyState,
    answer: str,
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
    return UserQuestion(
        question=action.question or (_question_for_field(field) if field is not None else "Envoy needs your input before continuing."),
        context=action.reason,
        suggested_answers=field.options if field is not None else [],
        target_element_id=action.element_id,
        question_key=_question_key_for_action(action),
    )


def _user_questions_for_pause(
    observation: PageObservation,
    current_action: ProposedAction,
    remaining_actions: list[ProposedAction],
    policy: PolicyDecision,
) -> list[UserQuestion]:
    questions: list[UserQuestion] = []
    first_question = _user_question_for_action(current_action, observation) or _user_question_for_policy(policy, current_action, observation)
    if first_question is not None:
        questions.append(first_question)

    seen_targets = {question.target_element_id for question in questions}
    for action in remaining_actions:
        if action.action_type != "ask_user":
            continue
        question = _user_question_for_action(action, observation)
        if question is None:
            continue
        if question.target_element_id in seen_targets:
            continue
        questions.append(question)
        seen_targets.add(question.target_element_id)
    return questions


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


def _observed_field(observation: PageObservation | None, element_id: str | None) -> Any | None:
    if observation is None or not element_id:
        return None
    return next((field for field in observation.fields if field.element_id == element_id), None)


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
