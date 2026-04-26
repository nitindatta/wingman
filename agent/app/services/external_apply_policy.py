"""Deterministic policy gate for external apply actions."""

from __future__ import annotations

import re
from typing import Any

from app.state.external_apply import ObservedField, PageObservation, PolicyDecision, ProposedAction

_EXECUTABLE_ACTIONS = {
    "fill_text",
    "select_option",
    "set_checkbox",
    "set_radio",
    "upload_file",
    "click",
}
_SENSITIVE_PATTERNS = [
    ("salary", r"\bsalary\b"),
    ("compensation", r"\bcompensation\b"),
    ("visa", r"\bvisa\b"),
    ("sponsorship", r"\bsponsor"),
    ("right_to_work", r"\bright to work\b"),
    ("work_rights", r"\bwork rights\b"),
    ("disability", r"\bdisability\b"),
    ("veteran", r"\bveteran\b"),
    ("gender", r"\bgender\b"),
    ("ethnicity", r"\bethnicity\b"),
    ("criminal", r"\bcriminal\b"),
    ("background_check", r"\bbackground check\b"),
    ("declaration", r"\bdeclaration\b"),
]


def validate_external_apply_action(
    *,
    observation: PageObservation,
    proposed_action: ProposedAction,
    profile_facts: dict[str, Any] | None = None,
) -> PolicyDecision:
    """Return allowed/paused/rejected for a planned action.

    The policy is intentionally conservative. It never tries to improve the
    planner's action; it only gates whether the proposed action may proceed.
    """

    action_type = proposed_action.action_type

    if action_type == "ask_user":
        return PolicyDecision(
            decision="paused",
            reason=proposed_action.reason or "Planner requested user input.",
            pause_reason="needs_user_input",
            risk_flags=["user_input_required"],
        )

    if action_type == "stop_ready_to_submit":
        return PolicyDecision(
            decision="paused",
            reason="Final submit requires explicit user approval.",
            pause_reason="final_submit",
            risk_flags=["final_submit_gate"],
        )

    if action_type == "stop_failed":
        return PolicyDecision(
            decision="rejected",
            reason=proposed_action.reason or "Planner indicated the flow cannot continue safely.",
            risk_flags=["planner_stop_failed"],
        )

    if action_type not in _EXECUTABLE_ACTIONS:
        return PolicyDecision(
            decision="rejected",
            reason=f"Unsupported action type: {action_type}",
            risk_flags=["unsupported_action"],
        )

    if not proposed_action.element_id:
        return PolicyDecision(
            decision="rejected",
            reason=f"{action_type} requires an element_id.",
            risk_flags=["missing_element_id"],
        )

    target_text = _target_text(observation, proposed_action.element_id)
    if target_text is None:
        return PolicyDecision(
            decision="rejected",
            reason=f"Target element was not present in the latest observation: {proposed_action.element_id}",
            risk_flags=["unknown_element"],
        )
    target_field = _target_field(observation, proposed_action.element_id)
    standard_privacy_consent = (
        target_field is not None
        and is_standard_privacy_consent_field(observation, target_field)
    )
    configured_consent_default = (
        target_field is not None
        and should_default_check_consent_field(observation, target_field, profile_facts or {})
    )

    if proposed_action.confidence < 0.75:
        return PolicyDecision(
            decision="paused",
            reason=f"Planner confidence {proposed_action.confidence:.2f} is below the auto-action threshold.",
            pause_reason="low_confidence",
            risk_flags=["low_confidence"],
        )

    if proposed_action.risk == "high":
        return PolicyDecision(
            decision="paused",
            reason="High-risk actions require user approval.",
            pause_reason="needs_approval",
            risk_flags=["high_risk"],
        )

    if action_type == "click" and _looks_like_utility_navigation_action(target_text):
        return PolicyDecision(
            decision="rejected",
            reason="Utility navigation links like skip/jump controls are not valid application actions.",
            risk_flags=["utility_navigation"],
        )

    sensitive_hits = _sensitive_hits(target_text)
    if sensitive_hits and not standard_privacy_consent and not configured_consent_default:
        return PolicyDecision(
            decision="paused",
            reason="The target appears to ask for sensitive or judgement-based information.",
            pause_reason="sensitive",
            risk_flags=sensitive_hits,
        )

    if action_type in {"fill_text", "select_option", "set_checkbox", "set_radio", "upload_file"}:
        if target_field and _looks_like_job_search_field(observation, target_field):
            return PolicyDecision(
                decision="paused",
                reason="The target looks like a job-search field, not an application form answer.",
                pause_reason="needs_approval",
                risk_flags=["not_application_form", "job_search_field"],
            )
        if (
            target_field
            and _looks_like_optional_or_judgement_consent(observation, target_field)
            and not configured_consent_default
        ):
            return PolicyDecision(
                decision="paused",
                reason="The checkbox appears to opt into an optional or judgement-based consent.",
                pause_reason="needs_approval",
                risk_flags=["optional_or_judgement_consent"],
            )
        if proposed_action.source not in {"profile", "memory", "user"}:
            return PolicyDecision(
                decision="paused",
                reason="Auto-fill requires an approved profile, memory, or user-provided source.",
                pause_reason="needs_approval",
                risk_flags=["unapproved_value_source"],
            )
        if proposed_action.value is None or proposed_action.value == "":
            return PolicyDecision(
                decision="rejected",
                reason=f"{action_type} requires a non-empty value.",
                risk_flags=["missing_value"],
            )
        if proposed_action.source == "profile" and target_field:
            expected_values = _profile_values_for_field(target_field, profile_facts or {})
            if expected_values and not _matches_any_expected_value(proposed_action.value, expected_values):
                return PolicyDecision(
                    decision="paused",
                    reason="Planner claimed a profile source, but the value does not match the profile fact for this field.",
                    pause_reason="needs_approval",
                    risk_flags=["profile_value_mismatch"],
                )

    if action_type == "click":
        if _looks_like_submit(target_text):
            return PolicyDecision(
                decision="paused",
                reason="Click target looks like final submission.",
                pause_reason="final_submit",
                risk_flags=["final_submit_gate"],
            )

    return PolicyDecision(
        decision="allowed",
        reason="Action passed deterministic policy checks.",
        risk_flags=[],
    )


def _target_text(observation: PageObservation, element_id: str) -> str | None:
    for field in observation.fields:
        if field.element_id == element_id:
            return " ".join([field.label, field.field_type, field.nearby_text])
    for button in observation.buttons:
        if button.element_id == element_id:
            return " ".join([button.label, button.nearby_text])
    for link in observation.links:
        if link.element_id == element_id:
            return " ".join([link.label, link.nearby_text])
    return None


def _target_field(observation: PageObservation, element_id: str | None) -> ObservedField | None:
    if not element_id:
        return None
    return next((field for field in observation.fields if field.element_id == element_id), None)


def _sensitive_hits(text: str) -> list[str]:
    lowered = text.lower()
    return [
        label
        for label, pattern in _SENSITIVE_PATTERNS
        if re.search(pattern, lowered)
    ]


def _looks_like_submit(text: str) -> bool:
    return bool(re.search(r"\b(submit|send application|apply now|finish application)\b", text.lower()))


def _looks_like_utility_navigation_action(text: str) -> bool:
    lowered = text.lower()
    return bool(re.search(r"\b(skip to main content|skip navigation|close jump menu|jump menu)\b", lowered))


def is_standard_privacy_consent_field(observation: PageObservation, field: ObservedField) -> bool:
    """Return True for required application consent that is safe to default-check.

    This intentionally excludes optional marketing, talent-pool, legal,
    background-check, diversity, and work-rights style declarations.
    """

    if field.field_type != "checkbox":
        return False

    text = " ".join([field.label, field.nearby_text, observation.visible_text[:1200]]).lower()
    required = field.required or "required" in text or "*" in field.label
    if not required:
        return False

    if re.search(
        r"\b(marketing|newsletter|job alert|talent pool|future opportunit|promotional|"
        r"background check|criminal|police check|disability|diversity|ethnicity|gender|"
        r"salary|compensation|visa|sponsor|right to work|work rights)\b",
        text,
    ):
        return False

    has_privacy_subject = re.search(
        r"\b(privacy|personal data|data protection|processing|store|stored|transferred|pageup)\b",
        text,
    )
    has_terms_subject = re.search(
        r"\b(terms and conditions|terms & conditions|terms of use|terms of service|application terms|create account|account creation|candidate account)\b",
        text,
    )
    has_general_consent_subject = re.search(
        r"\b(consent|agreement|agree|agrees|acknowledge|accept|accepted|authorise|authorize|permission|confirm|opt in)\b",
        text,
    )
    return bool(has_general_consent_subject and (has_privacy_subject or has_terms_subject or "required" in text or "*" in field.label))


def should_default_check_consent_field(
    observation: PageObservation,
    field: ObservedField,
    profile_facts: dict[str, Any],
) -> bool:
    if is_standard_privacy_consent_field(observation, field):
        return True
    if not consent_checkboxes_always_true(profile_facts):
        return False
    return _looks_like_any_consent_field(observation, field)


def _looks_like_optional_or_judgement_consent(observation: PageObservation, field: ObservedField) -> bool:
    if field.field_type != "checkbox":
        return False
    text = " ".join([field.label, field.nearby_text, observation.visible_text[:1200]]).lower()
    return bool(re.search(
        r"\b(marketing|newsletter|job alert|talent pool|future opportunit|promotional|"
        r"background check|criminal|police check|disability|diversity|ethnicity|gender|"
        r"salary|compensation|visa|sponsor|right to work|work rights)\b",
        text,
    ))


def _looks_like_any_consent_field(observation: PageObservation, field: ObservedField) -> bool:
    if field.field_type != "checkbox":
        return False
    text = " ".join([field.label, field.nearby_text, observation.visible_text[:1200]]).lower()
    return bool(re.search(
        r"\b(consent|agreement|agree|agrees|acknowledge|accept|accepted|authorise|authorize|permission|"
        r"confirm|opt in|privacy|terms and conditions|terms & conditions|terms of use|terms of service|"
        r"marketing|newsletter|job alert|talent pool|future opportunit|promotional)\b",
        text,
    ))


def consent_checkboxes_always_true(profile_facts: dict[str, Any]) -> bool:
    return any(
        _profile_truthy(profile_facts, path)
        for path in (
            "external_accounts.always_accept_consents",
            "external_accounts.auto_approve_consents",
            "external_accounts.consent_checkboxes",
            "external_accounts.consent_checkboxes_always_true",
            "always_accept_consents",
            "auto_approve_consents",
            "consent_checkboxes",
            "consent_checkboxes_always_true",
        )
    )


def _looks_like_job_search_field(observation: PageObservation, field: ObservedField) -> bool:
    label = field.label.strip().lower()
    combined_page_text = " ".join([observation.url, observation.title, observation.visible_text]).lower()
    if not re.search(r"\b(job search|perform a job search|suggestions will appear|classification list|saved searches)\b", combined_page_text):
        return False
    return (
        field.field_type in {"search"}
        or label in {"what", "where", "keyword", "keywords", "job title", "classification"}
        or re.search(r"\b(keyword|job title|classification|location)\b", label) is not None
    )


def _profile_values_for_field(field: ObservedField, profile_facts: dict[str, Any]) -> list[str]:
    label = " ".join([field.label, field.field_type, field.nearby_text]).lower()
    if "email" in label or "e-mail" in label:
        return _profile_values(profile_facts, ["email", "contact.email", "external_accounts.default.email", "default.email"])
    if re.search(r"\b(pass(word|code|phrase)?)\b", label):
        return _profile_values(profile_facts, ["password", "external_accounts.default.password", "default.password"])
    if re.search(r"\b(phone|mobile|telephone|tel)\b", label):
        return _profile_values(profile_facts, ["phone", "contact.phone"])
    if "linkedin" in label:
        return _profile_values(profile_facts, ["linkedin_url", "contact.linkedin", "contact.linkedin_url"])
    if field.field_type == "file" or re.search(r"\b(resume|resum[eé]|cv|curriculum vitae)\b", label):
        return _profile_values(profile_facts, ["resume_path"])
    if "full name" in label or label.strip() in {"name", "your name"}:
        return _profile_values(profile_facts, ["name", "full_name"])
    if "first name" in label:
        return _profile_values(profile_facts, ["first_name"])
    if "last name" in label or "surname" in label:
        return _profile_values(profile_facts, ["last_name"])
    if "address line two" in label or "address line 2" in label:
        return []
    if "home address" in label or "street address" in label or re.search(r"\baddress\b", label):
        return _profile_values(profile_facts, ["address.street", "address.formatted"])
    if "postcode" in label or "post code" in label or "zip" in label:
        return _profile_values(profile_facts, ["address.postcode"])
    if "state" in label or "province" in label:
        return _profile_values(profile_facts, ["address.state_code", "address.state"])
    if "country" in label:
        return _profile_values(profile_facts, ["address.country"])
    if "location" in label or "city" in label or "suburb" in label or "town" in label:
        return _profile_values(profile_facts, ["location", "city", "address.suburb"])
    return []


def _profile_values(profile_facts: dict[str, Any], paths: list[str]) -> list[str]:
    values: list[str] = []
    for path in paths:
        current: Any = profile_facts
        for part in path.split("."):
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(part)
        if current is not None:
            text = str(current).strip()
            if text:
                values.append(text)
    return values


def _profile_truthy(profile_facts: dict[str, Any], path: str) -> bool:
    current: Any = profile_facts
    for part in path.split("."):
        if not isinstance(current, dict):
            return False
        current = current.get(part)
    if isinstance(current, bool):
        return current
    if isinstance(current, str):
        return current.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(current)


def _matches_any_expected_value(value: str, expected_values: list[str]) -> bool:
    normalized_value = _normalize_profile_value(value)
    return any(normalized_value == _normalize_profile_value(expected) for expected in expected_values)


def _normalize_profile_value(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()
