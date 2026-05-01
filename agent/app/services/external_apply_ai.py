"""LLM planner for Envoy's external apply harness.

The planner never executes browser actions; it proposes actions that the
deterministic harness policy and Playwright tools handle later.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from app.services.run_events import current_node as _current_node
from app.services.run_events import current_run_id as _current_run_id
from app.services.run_events import emit as _emit
from app.settings import Settings
from app.state.external_apply import ActionTrace, PageObservation, ProposedAction

log = logging.getLogger("external_apply_ai")
_TRANSCRIPT_LOG_PATH = Path(__file__).resolve().parents[3] / "logs" / "external_apply_llm.jsonl"
_REDACTED = "[REDACTED]"

_BROWSER_ACTIONS = {
    "fill_text",
    "select_option",
    "set_checkbox",
    "set_radio",
    "upload_file",
    "click",
}
_STOP_ACTIONS = {"ask_user", "stop_ready_to_submit", "stop_failed"}
_ALL_ACTIONS = _BROWSER_ACTIONS | _STOP_ACTIONS
_SENSITIVE_WORDS = {
    "salary",
    "compensation",
    "visa",
    "sponsorship",
    "work rights",
    "right to work",
    "disability",
    "veteran",
    "gender",
    "ethnicity",
    "criminal",
    "background check",
    "declaration",
}
_NONFINAL_APPLY_LABELS = re.compile(
    r"\b(apply(?: now| manually)?|start application|begin application|continue application|continue applying)\b"
)
_ACCOUNT_CREATION_LABELS = re.compile(r"\b(create account|register|sign up|sign-up)\b")
_NAVIGATION_LABELS = re.compile(r"\b(continue|next|save and continue|proceed)\b")
_LOGIN_LABELS = re.compile(r"\b(sign in|sign-in|log in|login)\b")
_FINAL_SUBMIT_LABELS = re.compile(r"\b(submit|send application)\b")
_FINAL_APPLY_LABELS = re.compile(r"\bapply now\b")
_MANUAL_ENTRY_LABELS = re.compile(
    r"\b(type it in myself|enter manually|manual entry|fill (?:it )?in myself|type manually)\b"
)
_RESUME_UPLOAD_LABELS = re.compile(r"\b(resume|resum[eé]|cv|curriculum vitae)\b")
_SECRET_KEY_PATTERN = re.compile(
    r"(?:^|_|\b)(password|passcode|passphrase|secret|token|otp|mfa|api[_-]?key|authorization)(?:$|_|\b)",
    re.IGNORECASE,
)


def _build_client(settings: Settings) -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
    )


async def propose_external_apply_action(
    settings: Settings,
    *,
    observation: PageObservation,
    profile_facts: dict[str, Any] | None = None,
    approved_memory: list[dict[str, Any]] | None = None,
    recent_actions: list[ActionTrace] | None = None,
    memory_context: dict[str, Any] | None = None,
    planning_frame: dict[str, Any] | None = None,
) -> ProposedAction:
    """Return one proposed action for the current external apply page."""

    client = _build_client(settings)
    system, user = build_external_apply_planner_messages(
        observation=observation,
        profile_facts=profile_facts or {},
        approved_memory=approved_memory or [],
        recent_actions=recent_actions or [],
        memory_context=memory_context or {},
        planning_frame=planning_frame or {},
    )
    _emit("llm_prompt", f"plan action: {observation.page_type} @ {observation.url[:60]}", {
        "call": "propose_action",
        "model": settings.openai_model,
        "url": observation.url,
        "page_type": observation.page_type,
        "fields_count": len(observation.fields),
        "buttons_count": len(observation.buttons),
        "recent_actions_count": len(recent_actions or []),
        "system": system[:400],
        "user": user[:800],
    })
    try:
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            max_tokens=360,
        )
        raw = response.choices[0].message.content or "{}"
        action = parse_planner_response(raw, observation)
        _append_external_apply_llm_transcript(
            call="propose_action",
            model=settings.openai_model,
            observation=observation,
            system=system,
            user=user,
            raw_response=raw,
            parsed_response=action.model_dump(),
        )
        _emit("llm_response", f"planned: {action.action_type}", {
            "call": "propose_action",
            "raw": raw[:400],
            "action_type": action.action_type,
            "element_id": action.element_id,
            "value": (action.value or "")[:80],
            "confidence": action.confidence,
            "risk": action.risk,
            "reason": action.reason,
            "source": action.source,
        })
        return action
    except Exception as exc:  # pragma: no cover - network/runtime dependent
        log.warning("[propose_external_apply_action] falling back: %s", exc)
        return fallback_proposed_action(observation, profile_facts or {}, approved_memory or [])


async def propose_external_apply_actions(
    settings: Settings,
    *,
    observation: PageObservation,
    profile_facts: dict[str, Any] | None = None,
    approved_memory: list[dict[str, Any]] | None = None,
    recent_actions: list[ActionTrace] | None = None,
    memory_context: dict[str, Any] | None = None,
    planning_frame: dict[str, Any] | None = None,
) -> list[ProposedAction]:
    """Return a page-level action plan for the current external apply page."""

    client = _build_client(settings)
    system, user = build_external_apply_batch_planner_messages(
        observation=observation,
        profile_facts=profile_facts or {},
        approved_memory=approved_memory or [],
        recent_actions=recent_actions or [],
        memory_context=memory_context or {},
        planning_frame=planning_frame or {},
    )
    _emit("llm_prompt", f"batch plan: {observation.page_type} @ {observation.url[:60]}", {
        "call": "batch_plan",
        "model": settings.openai_model,
        "url": observation.url,
        "page_type": observation.page_type,
        "fields_count": len(observation.fields),
        "buttons_count": len(observation.buttons),
        "recent_actions_count": len(recent_actions or []),
        "system": system[:400],
        "user": user[:800],
    })
    try:
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            max_tokens=1200,
        )
        raw = response.choices[0].message.content or "{}"
        actions = parse_planner_batch_response(raw, observation)
        _append_external_apply_llm_transcript(
            call="batch_plan",
            model=settings.openai_model,
            observation=observation,
            system=system,
            user=user,
            raw_response=raw,
            parsed_response={"actions": [action.model_dump() for action in actions]},
        )
        _emit("llm_response", f"batch plan: {len(actions)} actions", {
            "call": "batch_plan",
            "raw": raw[:600],
            "actions_count": len(actions),
            "actions": [
                {"action_type": a.action_type, "element_id": a.element_id, "confidence": a.confidence, "risk": a.risk}
                for a in actions
            ],
        })
        return actions
    except Exception as exc:  # pragma: no cover - network/runtime dependent
        log.warning("[propose_external_apply_actions] falling back: %s", exc)
        return fallback_proposed_actions(observation, profile_facts or {}, approved_memory or [])


def build_external_apply_planner_messages(
    *,
    observation: PageObservation,
    profile_facts: dict[str, Any],
    approved_memory: list[dict[str, Any]],
    recent_actions: list[ActionTrace],
    memory_context: dict[str, Any] | None = None,
    planning_frame: dict[str, Any] | None = None,
) -> tuple[str, str]:
    system = (
        "You are Envoy's external apply planning agent. "
        "You do not operate the browser. You propose exactly one next browser action as JSON. "
        "Use only observed element_id values from page.fields, page.uploads, page.buttons, or page.links. "
        "Follow planning_frame strategies, hints, recommended_actions, and blocked_actions. "
        "Use only available_facts, approved_memory, or explicit user-sourced recent actions for form values. "
        "For career narrative questions such as why you are interested, why this opportunity, or how your "
        "skills, experience, and passion fit the role, draft a concise tailored answer grounded only in "
        "available_facts and observed page/job context; use source profile or inferred. "
        "Ask the user when required information is missing, sensitive, a personal self-report, or ambiguous. "
        "When asking the user, ask for exactly one observed field per ask_user action; do not bundle multiple fields "
        "or multiple answers into one question. "
        "Never final-submit; return stop_ready_to_submit at the final submission gate. "
        "Return ONLY valid JSON with this shape: "
        "{\"action_type\":\"fill_text|select_option|set_checkbox|set_radio|upload_file|click|ask_user|stop_ready_to_submit|stop_failed\","
        "\"element_id\":\"... or null\",\"value\":\"... or null\",\"question\":\"... or null\","
        "\"confidence\":0.0,\"risk\":\"low|medium|high\",\"reason\":\"...\","
        "\"source\":\"profile|memory|user|inferred|page|none\"}."
    )
    payload = {
        "page": _observation_for_prompt(observation),
        "planning_frame": planning_frame or {},
        "available_facts": _available_facts_for_prompt(profile_facts),
        "memory_context": memory_context or {},
        "approved_memory": approved_memory[:12],
        "recent_actions": [_trace_for_prompt(trace) for trace in recent_actions[-6:]],
        "allowed_actions": sorted(_ALL_ACTIONS),
    }
    user = json.dumps(_redact_prompt_value(payload), ensure_ascii=False, indent=2)
    return system, user


def build_external_apply_batch_planner_messages(
    *,
    observation: PageObservation,
    profile_facts: dict[str, Any],
    approved_memory: list[dict[str, Any]],
    recent_actions: list[ActionTrace],
    memory_context: dict[str, Any] | None = None,
    planning_frame: dict[str, Any] | None = None,
) -> tuple[str, str]:
    system = (
        "You are Envoy's external apply planning agent. "
        "You do not operate the browser. You propose a page plan as JSON. "
        "Use only observed element_id values from page.fields, page.uploads, page.buttons, or page.links. "
        "Follow planning_frame strategies, hints, recommended_actions, and blocked_actions. "
        "Use only available_facts, approved_memory, or explicit user-sourced recent actions for form values. "
        "Prefer safe field actions before navigation, skip fields that already have useful current_value, "
        "and draft career narrative answers for questions like why you are interested, why this opportunity, "
        "or how your skills, experience, and passion fit the role when available_facts and observed page/job "
        "context are enough. Use source profile or inferred for those grounded narrative answers. "
        "Ask the user for required information that is missing, sensitive, a personal self-report, or ambiguous. "
        "When asking the user, ask for exactly one observed field per ask_user action; return multiple ask_user "
        "actions when multiple fields need user input, and do not bundle multiple answers into one question. "
        "Do not include click, stop_ready_to_submit, or stop_failed after field actions in the same page plan; "
        "the harness will re-observe after field changes. "
        "Never final-submit; return stop_ready_to_submit at the final submission gate. "
        "Return ONLY valid JSON with this shape: "
        "{\"actions\":[{\"action_type\":\"fill_text|select_option|set_checkbox|set_radio|upload_file|click|ask_user|stop_ready_to_submit|stop_failed\","
        "\"element_id\":\"... or null\",\"value\":\"... or null\",\"question\":\"... or null\","
        "\"confidence\":0.0,\"risk\":\"low|medium|high\",\"reason\":\"...\","
        "\"source\":\"profile|memory|user|inferred|page|none\"}]}. "
        "Return at most 12 actions."
    )
    payload = {
        "page": _observation_for_prompt(observation),
        "planning_frame": planning_frame or {},
        "available_facts": _available_facts_for_prompt(profile_facts),
        "memory_context": memory_context or {},
        "approved_memory": approved_memory[:12],
        "recent_actions": [_trace_for_prompt(trace) for trace in recent_actions[-8:]],
        "allowed_actions": sorted(_ALL_ACTIONS),
    }
    user = json.dumps(_redact_prompt_value(payload), ensure_ascii=False, indent=2)
    return system, user


def parse_planner_response(raw: str, observation: PageObservation) -> ProposedAction:
    parsed = _parse_json_object(raw)
    if not isinstance(parsed, dict):
        raise ValueError("planner response was not a JSON object")

    action_type = str(parsed.get("action_type", "")).strip()
    if action_type not in _ALL_ACTIONS:
        raise ValueError(f"unsupported action_type: {action_type}")

    element_id = _optional_string(parsed.get("element_id"))
    if action_type in _BROWSER_ACTIONS:
        if not element_id:
            raise ValueError(f"{action_type} requires element_id")
        observed_ids = _observed_element_ids(observation)
        if element_id not in observed_ids:
            raise ValueError(f"unknown element_id: {element_id}")

    proposed = ProposedAction(
        action_type=action_type,  # type: ignore[arg-type]
        element_id=element_id,
        value=_optional_string(parsed.get("value")),
        question=_optional_string(parsed.get("question")),
        confidence=_coerce_confidence(parsed.get("confidence")),
        risk=_coerce_risk(parsed.get("risk")),
        reason=str(parsed.get("reason", "")).strip() or "Planner proposed the next action.",
        source=_coerce_source(parsed.get("source")),
    )
    return proposed


def parse_planner_batch_response(raw: str, observation: PageObservation) -> list[ProposedAction]:
    parsed = _parse_json_object(raw)
    if isinstance(parsed, dict):
        raw_actions = parsed.get("actions")
    else:
        raw_actions = parsed
    if not isinstance(raw_actions, list):
        raise ValueError("batch planner response must contain an actions array")

    actions: list[ProposedAction] = []
    for item in raw_actions[:12]:
        if not isinstance(item, dict):
            raise ValueError("batch planner action was not a JSON object")
        actions.append(_parse_planner_action(item, observation))
    if not actions:
        raise ValueError("batch planner returned no actions")
    return actions


def _parse_planner_action(parsed: dict[str, Any], observation: PageObservation) -> ProposedAction:
    action_type = str(parsed.get("action_type", "")).strip()
    if action_type not in _ALL_ACTIONS:
        raise ValueError(f"unsupported action_type: {action_type}")

    element_id = _optional_string(parsed.get("element_id"))
    if action_type in _BROWSER_ACTIONS:
        if not element_id:
            raise ValueError(f"{action_type} requires element_id")
        observed_ids = _observed_element_ids(observation)
        if element_id not in observed_ids:
            raise ValueError(f"unknown element_id: {element_id}")

    return ProposedAction(
        action_type=action_type,  # type: ignore[arg-type]
        element_id=element_id,
        value=_optional_string(parsed.get("value")),
        question=_optional_string(parsed.get("question")),
        confidence=_coerce_confidence(parsed.get("confidence")),
        risk=_coerce_risk(parsed.get("risk")),
        reason=str(parsed.get("reason", "")).strip() or "Planner proposed the next action.",
        source=_coerce_source(parsed.get("source")),
    )


def fallback_proposed_action(
    observation: PageObservation,
    profile_facts: dict[str, Any],
    approved_memory: list[dict[str, Any]],
) -> ProposedAction:
    """Conservative deterministic fallback when the LLM fails or is unavailable."""

    if observation.page_type == "captcha":
        return ProposedAction(
            action_type="ask_user",
            question="This page appears to contain a captcha. Please complete it manually, then continue.",
            confidence=0.95,
            risk="high",
            reason="Captcha pages require human action.",
            source="page",
        )

    if observation.page_type == "confirmation":
        return ProposedAction(
            action_type="stop_failed",
            confidence=0.8,
            risk="low",
            reason="The page looks like an application confirmation page.",
            source="page",
        )

    if _looks_like_job_search_page(observation):
        return ProposedAction(
            action_type="ask_user",
            question="This looks like a job-search page rather than the employer application form. Please navigate to the actual application page, then continue.",
            confidence=0.92,
            risk="medium",
            reason="The page contains job-search fields such as keyword/location instead of application form questions.",
            source="page",
        )

    for field in observation.fields:
        if field.disabled or (field.current_value and not getattr(field, "invalid", False)):
            continue
        label = field.label.lower()
        if _is_sensitive(label):
            return ProposedAction(
                action_type="ask_user",
                element_id=field.element_id,
                question=f"How should I answer: {field.label}?",
                confidence=0.9,
                risk="medium",
                reason="This field may require user judgement or confirmation.",
                source="page",
            )
        value, source = _lookup_safe_value(field.label, field.field_type, profile_facts, approved_memory)
        if value:
            return _action_for_field(field.element_id, field.field_type, value, source)

    for action in _iter_unique_observed_actions(observation):
        if action.disabled:
            continue
        if _is_final_submit_action(action, observation):
            return ProposedAction(
                action_type="stop_ready_to_submit",
                element_id=action.element_id,
                confidence=0.9,
                risk="high",
                reason="Final submission must go through the portal approval gate.",
                source="page",
            )

    navigation_action = _fallback_navigation_action(observation)
    if navigation_action is not None:
        return navigation_action

    return ProposedAction(
        action_type="ask_user",
        question="I could not confidently determine the next safe action on this page. What should I do next?",
        confidence=0.75,
        risk="medium",
        reason="No safe deterministic action was available.",
        source="page",
    )


def fallback_proposed_actions(
    observation: PageObservation,
    profile_facts: dict[str, Any],
    approved_memory: list[dict[str, Any]],
) -> list[ProposedAction]:
    if observation.page_type in {"captcha", "confirmation"} or _looks_like_job_search_page(observation):
        return [fallback_proposed_action(observation, profile_facts, approved_memory)]

    safe_actions: list[ProposedAction] = []
    user_questions: list[ProposedAction] = []

    for field in observation.fields:
        if field.disabled or (field.current_value and not getattr(field, "invalid", False)):
            continue
        label = field.label.lower()
        if _is_sensitive(label):
            user_questions.append(
                ProposedAction(
                    action_type="ask_user",
                    element_id=field.element_id,
                    question=f"How should I answer: {field.label}?",
                    confidence=0.9,
                    risk="medium",
                    reason="This field may require user judgement or confirmation.",
                    source="page",
                )
            )
            continue

        value, source = _lookup_safe_value(field.label, field.field_type, profile_facts, approved_memory)
        if value:
            safe_actions.append(_action_for_field(field.element_id, field.field_type, value, source))
            continue

        if field.required:
            user_questions.append(
                ProposedAction(
                    action_type="ask_user",
                    element_id=field.element_id,
                    question=f"How should I answer: {field.label}?",
                    confidence=0.82,
                    risk="medium",
                    reason="No approved exact answer is available for this required field.",
                    source="page",
                )
            )

    if user_questions:
        return [*safe_actions, *user_questions[:8]]
    if safe_actions:
        return safe_actions

    return [fallback_proposed_action(observation, profile_facts, approved_memory)]


def _observation_for_prompt(observation: PageObservation) -> dict[str, Any]:
    return {
        "url": observation.url,
        "title": observation.title,
        "page_type": observation.page_type,
        "visible_text_excerpt": observation.visible_text[:2500],
        "fields": [field.model_dump() for field in observation.fields[:30]],
        "uploads": [field.model_dump() for field in observation.uploads[:10]],
        "buttons": [button.model_dump() for button in observation.buttons[:20]],
        "links": [link.model_dump() for link in observation.links[:12]],
        "errors": observation.errors[:10],
    }


def _available_facts_for_prompt(profile_facts: dict[str, Any]) -> dict[str, Any]:
    allowed_top_level = {
        "name",
        "full_name",
        "first_name",
        "last_name",
        "headline",
        "summary",
        "email",
        "phone",
        "linkedin_url",
        "location",
        "city",
        "contact",
        "address",
        "work_rights",
        "core_strengths",
        "skills",
        "experience",
        "employment_history",
        "selected_projects",
        "projects",
        "evidence_items",
        "voice_profile",
        "voice_samples",
        "proposal_preferences",
        "external_accounts",
        "resume_path",
    }
    return {
        key: _compact_prompt_fact(value)
        for key, value in profile_facts.items()
        if key in allowed_top_level and value not in (None, "", [], {})
    }


def _compact_prompt_fact(value: Any, *, depth: int = 0) -> Any:
    if isinstance(value, dict):
        if depth >= 4:
            return {}
        return {
            str(key): _compact_prompt_fact(child, depth=depth + 1)
            for key, child in value.items()
            if child not in (None, "", [], {})
        }
    if isinstance(value, list):
        if depth >= 4:
            return []
        limit = 8 if depth == 0 else 5
        return [_compact_prompt_fact(item, depth=depth + 1) for item in value[:limit]]
    if isinstance(value, str):
        return _short_text(value)
    if depth >= 4:
        return _short_text(value)
    return value


def _short_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text[:900]


def _redact_prompt_value(value: Any, *, key_path: str = "") -> Any:
    if isinstance(value, dict):
        has_secret_context = _dict_has_secret_context(value)
        redacted: dict[str, Any] = {}
        for key, child in value.items():
            child_path = f"{key_path}.{key}" if key_path else str(key)
            if _is_secret_key(str(key)):
                continue
            if has_secret_context and str(key) in {"answer", "current_value", "value", "value_after"}:
                redacted[str(key)] = _REDACTED
                continue
            child_value = _redact_prompt_value(child, key_path=child_path)
            if child_value not in (None, "", [], {}):
                redacted[str(key)] = child_value
        return redacted
    if isinstance(value, list):
        return [_redact_prompt_value(item, key_path=key_path) for item in value]
    return value


def _is_secret_key(key: str) -> bool:
    return bool(_SECRET_KEY_PATTERN.search(key))


def _dict_has_secret_context(value: dict[str, Any]) -> bool:
    context = " ".join(
        str(value.get(key, ""))
        for key in ("element_id", "field_id", "id", "label", "question", "name", "nearby_text")
    )
    return bool(_SECRET_KEY_PATTERN.search(context))


def _append_external_apply_llm_transcript(
    *,
    call: str,
    model: str,
    observation: PageObservation,
    system: str,
    user: str,
    raw_response: str,
    parsed_response: dict[str, Any],
) -> None:
    try:
        _TRANSCRIPT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": _current_run_id(),
            "node": _current_node(),
            "call": call,
            "model": model,
            "page": {
                "url": observation.url,
                "title": observation.title,
                "page_type": observation.page_type,
                "fields_count": len(observation.fields),
                "buttons_count": len(observation.buttons),
            },
            "request": {
                "system": system,
                "user": _redact_json_text(user),
            },
            "response": {
                "raw": _redact_json_text(raw_response),
                "parsed": _redact_prompt_value(parsed_response),
            },
        }
        with _TRANSCRIPT_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # pragma: no cover - transcript logging must not break planning
        log.warning("[external_apply_ai] failed to write LLM transcript: %s", exc)


def _redact_json_text(text: str) -> str:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    return json.dumps(_redact_prompt_value(parsed), ensure_ascii=False, indent=2)


def _trace_for_prompt(trace: ActionTrace) -> dict[str, Any]:
    return {
        "action": trace.proposed_action.model_dump(),
        "policy_decision": trace.policy_decision,
        "result": trace.result.model_dump() if trace.result else None,
    }


def _parse_json_object(raw: str) -> Any:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    return json.loads(text)


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.0
    return max(0.0, min(1.0, confidence))


def _coerce_risk(value: object) -> str:
    risk = str(value or "").strip().lower()
    return risk if risk in {"low", "medium", "high"} else "medium"


def _coerce_source(value: object) -> str:
    source = str(value or "").strip().lower()
    allowed = {"profile", "memory", "user", "inferred", "page", "none"}
    return source if source in allowed else "none"


def _observed_element_ids(observation: PageObservation) -> set[str]:
    return {
        *(field.element_id for field in observation.fields),
        *(button.element_id for button in observation.buttons),
        *(link.element_id for link in observation.links),
    }


def _iter_unique_observed_actions(observation: PageObservation) -> list[Any]:
    seen_ids: set[str] = set()
    deduped: list[Any] = []
    for action in [*observation.buttons, *observation.links]:
        if action.element_id in seen_ids:
            continue
        seen_ids.add(action.element_id)
        deduped.append(action)
    return deduped


def _is_sensitive(label: str) -> bool:
    return any(word in label for word in _SENSITIVE_WORDS)


def _is_final_submit_action(action: Any, observation: PageObservation) -> bool:
    label = action.label.lower()
    if _FINAL_SUBMIT_LABELS.search(label):
        return True
    if not _FINAL_APPLY_LABELS.search(label):
        return False
    return observation.page_type in {"review", "final_submit"}


def _fallback_navigation_action(observation: PageObservation) -> ProposedAction | None:
    best_action: Any | None = None
    best_score = 0
    for action in _iter_unique_observed_actions(observation):
        if action.disabled:
            continue
        score = _navigation_action_score(action, observation)
        if score <= best_score:
            continue
        best_action = action
        best_score = score
    if best_action is None:
        return None
    return ProposedAction(
        action_type="click",
        element_id=best_action.element_id,
        confidence=0.68,
        risk="medium",
        reason="No safe fillable fields were found; this looks like the next application step.",
        source="page",
    )


def _navigation_action_score(action: Any, observation: PageObservation) -> int:
    label = action.label.lower()
    href = (action.href or "").lower()

    if _NONFINAL_APPLY_LABELS.search(label):
        score = 100
    elif _MANUAL_ENTRY_LABELS.search(label):
        score = 95
    elif _ACCOUNT_CREATION_LABELS.search(label):
        score = 90
    elif _NAVIGATION_LABELS.search(label):
        score = 80
    elif _LOGIN_LABELS.search(label):
        score = 70
    else:
        return 0

    if href and "/apply" in href:
        score += 5
    if href and "/login" in href and score < 90:
        score -= 5
    if observation.page_type == "login" and _LOGIN_LABELS.search(label):
        score += 10
    if observation.page_type == "unknown" and _NONFINAL_APPLY_LABELS.search(label):
        score += 5
    return score


def _looks_like_resume_upload_label(label: str) -> bool:
    return bool(_RESUME_UPLOAD_LABELS.search(label.lower()))


def _looks_like_job_search_page(observation: PageObservation) -> bool:
    page_text = " ".join([observation.url, observation.title, observation.visible_text]).lower()
    if not re.search(r"\b(job search|perform a job search|suggestions will appear|classification list|saved searches)\b", page_text):
        return False
    search_labels = {"what", "where", "keyword", "keywords", "job title", "classification"}
    return any(
        field.field_type == "search"
        or field.label.strip().lower() in search_labels
        for field in observation.fields
    )


def _lookup_safe_value(
    label: str,
    field_type: str,
    profile_facts: dict[str, Any],
    approved_memory: list[dict[str, Any]],
) -> tuple[str | None, str]:
    label_lower = label.lower()
    if _looks_like_resume_upload_label(label_lower):
        value = _profile_path_value(profile_facts, "resume_path")
        if value:
            return str(value), "profile"

    if "address line two" in label_lower or "address line 2" in label_lower:
        return None, "none"

    if re.search(r"\b(pass(word|code|phrase)?)\b", label_lower):
        value = _first_profile_value(
            profile_facts,
            [
                "password",
                "external_accounts.default.password",
                "default.password",
            ],
        )
        if value:
            return str(value), "profile"

    prior_employment = _prior_employment_answer(label, profile_facts)
    if prior_employment is not None:
        return prior_employment, "profile"

    if re.search(r"\b(how did you hear|heard about|source)\b", label_lower):
        value = _first_profile_value(
            profile_facts,
            [
                "heard_about",
                "external_accounts.default.heard_about",
                "default.heard_about",
            ],
        )
        if value:
            return str(value), "profile"

    if re.search(r"\b(salutation|honorific)\b", label_lower):
        value = _first_profile_value(
            profile_facts,
            [
                "salutation",
                "external_accounts.default.salutation",
                "default.salutation",
            ],
        )
        if value:
            return str(value), "profile"

    if re.search(r"\b(phone device type|device type)\b", label_lower):
        value = _first_profile_value(
            profile_facts,
            [
                "phone_device_type",
                "external_accounts.default.phone_device_type",
                "default.phone_device_type",
            ],
        )
        if value:
            return str(value), "profile"

    mappings = [
        (("first name",), "first_name"),
        (("last name",), "last_name"),
        (("full name", "name"), "name"),
        (("email", "e-mail"), "email"),
        (("phone", "mobile", "telephone"), "phone"),
        (("home address", "street address", "address"), "address.street"),
        (("city", "suburb", "town"), "address.suburb"),
        (("postcode", "post code", "zip"), "address.postcode"),
        (("state", "province"), "address.state_code"),
        (("country",), "address.country"),
        (("city",), "city"),
        (("location", "suburb"), "location"),
        (("linkedin",), "linkedin_url"),
    ]
    for keys, profile_key in mappings:
        if any(key in label_lower for key in keys):
            value = _first_profile_value(profile_facts, _profile_lookup_paths(profile_key))
            if value:
                return str(value), "profile"

    for item in approved_memory:
        question = str(item.get("question", item.get("label", ""))).lower()
        if question and (question in label_lower or label_lower in question):
            answer = item.get("answer") or item.get("value")
            if answer:
                return str(answer), "memory"
    return None, "none"


def _profile_path_value(profile_facts: dict[str, Any], path: str) -> Any:
    current: Any = profile_facts
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _first_profile_value(profile_facts: dict[str, Any], paths: list[str]) -> Any:
    for path in paths:
        value = _profile_path_value(profile_facts, path)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _profile_lookup_paths(profile_key: str) -> list[str]:
    mapping: dict[str, list[str]] = {
        "email": ["email", "contact.email", "external_accounts.default.email", "default.email"],
        "phone": ["phone", "contact.phone"],
        "linkedin_url": ["linkedin_url", "contact.linkedin", "contact.linkedin_url"],
        "name": ["name", "full_name"],
        "first_name": ["first_name"],
        "last_name": ["last_name"],
        "address.street": ["address.street", "address.formatted"],
        "address.suburb": ["address.suburb", "city", "location"],
        "address.postcode": ["address.postcode"],
        "address.state_code": ["address.state_code", "address.state"],
        "address.country": ["address.country"],
        "city": ["city", "address.suburb", "location"],
        "location": ["location", "city", "address.suburb"],
    }
    return mapping.get(profile_key, [profile_key])


def _prior_employment_answer(label: str, profile_facts: dict[str, Any]) -> str | None:
    if not re.search(r"\b(previously worked|worked at|worked for|current employee|previous employee|employed by)\b", label.lower()):
        return None
    employer = _extract_employer_name(label)
    if not employer:
        return None
    prior_employers = _employment_history_employers(profile_facts)
    if not prior_employers:
        return None
    normalized_target = _normalize_org_name(employer)
    if not normalized_target:
        return None
    matched = any(
        _normalize_org_name(company) == normalized_target
        or _normalize_org_name(company) in normalized_target
        or normalized_target in _normalize_org_name(company)
        for company in prior_employers
    )
    return "Yes" if matched else "No"


def _extract_employer_name(label: str) -> str:
    text = label.strip().rstrip("?:. ")
    patterns = [
        r"(?:have you previously worked at|have you worked at|have you worked for)\s+(.+)",
        r"(?:are you a current employee of|are you a previous employee of|are you currently employed by)\s+(.+)",
        r"(?:previously employed by|currently employed by)\s+(.+)",
    ]
    lowered = text.lower()
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if not match:
            continue
        start = match.start(1)
        employer = text[start:].strip()
        return re.sub(r"\s+", " ", employer).strip(" ?.:")
    return ""


def _employment_history_employers(profile_facts: dict[str, Any]) -> list[str]:
    history = _profile_path_value(profile_facts, "employment_history.employers")
    if not isinstance(history, list):
        history = _profile_path_value(profile_facts, "external_accounts.employment_history.employers")
    if not isinstance(history, list):
        return []
    return [str(item).strip() for item in history if str(item).strip()]


def _normalize_org_name(value: str) -> str:
    text = value.lower()
    text = text.replace("&", " and ")
    text = re.sub(r"\(.*?\)", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _action_for_field(
    element_id: str,
    field_type: str,
    value: str,
    source: str,
) -> ProposedAction:
    if field_type == "select":
        action_type = "select_option"
    elif field_type == "checkbox":
        action_type = "set_checkbox"
    elif field_type == "radio":
        action_type = "set_radio"
    elif field_type == "file":
        action_type = "upload_file"
    else:
        action_type = "fill_text"
    return ProposedAction(
        action_type=action_type,  # type: ignore[arg-type]
        element_id=element_id,
        value=value,
        confidence=0.82,
        risk="low",
        reason="Deterministic fallback matched a safe field to approved profile or memory data.",
        source=source,  # type: ignore[arg-type]
    )
