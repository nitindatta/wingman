"""Deterministic field insights for external application pages."""

from __future__ import annotations

import re
from typing import Any

from app.state.external_apply import (
    FieldAnswerability,
    FieldSensitivity,
    LabelQuality,
    ObservedField,
    PageObservation,
)


_OPTION_ONLY_LABELS = {
    "yes",
    "no",
    "male",
    "female",
    "select",
    "choose",
    "open",
    "required",
    "optional",
    "not applicable",
    "prefer not to answer",
    "prefer not to say",
}
_PROFILE_FACT_PATTERNS: list[tuple[str, str]] = [
    ("first_name", r"\b(first|given)\s+name\b"),
    ("last_name", r"\b(last|family)\s+name\b|\bsurname\b"),
    ("full_name", r"\bfull\s+name\b|^name$|\byour\s+name\b"),
    ("email", r"\be-?mail\b"),
    ("phone", r"\b(phone|mobile|telephone|tel)\b"),
    ("linkedin_url", r"\blinkedin\b"),
    ("working_rights", r"\b(right to work|work rights|authori[sz]ed to work|hold.*work)\b"),
    ("address.street", r"\b(home\s+address|street\s+address|address\s+line\s+1|\baddress\b)"),
    ("address.postcode", r"\b(post\s*code|postcode|zip)\b"),
    ("address.state", r"\b(state|province)\b"),
    ("address.country", r"\bcountry\b"),
    ("address.suburb", r"\b(city|suburb|town)\b"),
    ("salutation", r"\b(salutation|honou?rific|title)\b"),
    ("heard_about", r"\b(how did you hear|heard about|source)\b"),
    ("phone_device_type", r"\b(phone device type|device type)\b"),
    ("password", r"\b(pass(word|code|phrase)?)\b"),
]
_CAREER_NARRATIVE = re.compile(
    r"\b("
    r"why (?:are you )?(?:interested|applying)|"
    r"interested in (?:this|the) (?:role|opportunity|position)|"
    r"skills?, experience and passion|"
    r"hit the ground running|"
    r"make a difference|"
    r"(?:describe|outline|summari[sz]e|detail|tell us about|what is|what's) (?:your )?"
    r"(?:leadership|management|people leadership|technical leadership|experience|background)|"
    r"(?:experience|background) (?:using|with|in|of)|"
    r"how (?:many years'? )?(?:experience )?(?:do you have|have you used|have you worked|have you led)|"
    r"how have you used|"
    r"what .*experience .*with|"
    r"relevant (?:experience|examples?)|"
    r"past work"
    r")\b",
    re.IGNORECASE,
)


def enrich_page_observation(
    observation: PageObservation,
    profile_facts: dict[str, Any] | None = None,
    approved_memory: list[dict[str, Any]] | None = None,
) -> PageObservation:
    """Return a copy of an observation with deterministic field insights attached."""

    facts = profile_facts or {}
    memory = approved_memory or []
    enriched_fields = [
        _enrich_field(observation, field, facts, memory, index)
        for index, field in enumerate(observation.fields)
    ]
    enriched_by_id = {field.element_id: field for field in enriched_fields}
    enriched_uploads = [enriched_by_id.get(field.element_id, field) for field in observation.uploads]
    return observation.model_copy(update={"fields": enriched_fields, "uploads": enriched_uploads})


def observation_quality_issues(observation: PageObservation) -> list[str]:
    """Return compact quality issues that should be visible to planning."""

    issues: list[str] = []
    for field in observation.fields:
        if field.disabled or _field_has_useful_value(field):
            continue
        if field.required and field.label_quality in {"missing", "weak"}:
            issues.append(f"{field.element_id}: required field has {field.label_quality} label")
        if field.required and field.answerability == "unsafe_unknown":
            issues.append(f"{field.element_id}: required field cannot be safely classified")
    return issues[:8]


def _enrich_field(
    observation: PageObservation,
    field: ObservedField,
    profile_facts: dict[str, Any],
    approved_memory: list[dict[str, Any]],
    index: int,
) -> ObservedField:
    combined_text = _combined_text(field)
    label_quality = _label_quality(field)
    document_kind = _document_kind(observation, field, index)
    profile_fact = _profile_fact(field, document_kind)
    sensitivity = _sensitivity(field, profile_fact)
    answerability, reason = _answerability(
        field,
        label_quality=label_quality,
        document_kind=document_kind,
        profile_fact=profile_fact,
        sensitivity=sensitivity,
        profile_facts=profile_facts,
        approved_memory=approved_memory,
        combined_text=combined_text,
    )
    return field.model_copy(
        update={
            "label_quality": label_quality,
            "document_kind": document_kind,
            "profile_fact": profile_fact,
            "sensitivity": sensitivity,
            "answerability": answerability,
            "insight_reason": reason,
        }
    )


def _label_quality(field: ObservedField) -> LabelQuality:
    label = _normalise(field.label)
    if not label:
        return "missing"
    if label in _OPTION_ONLY_LABELS:
        return "weak"
    if _looks_like_noisy_script_or_job_text(field.label):
        return "weak"
    if _looks_like_control_transcript(" ".join([field.label, field.nearby_text])):
        return "weak"
    return "good"


def _document_kind(observation: PageObservation, field: ObservedField, index: int) -> str | None:
    if not _is_file_upload_field(field):
        return None
    text = _combined_text(field)
    if re.search(r"\b(resume|resum[eé]|cv|curriculum vitae)\b", text):
        return "resume"
    if re.search(r"\bcover\s+letter\b", text):
        return "cover_letter"
    if re.search(r"\b(other|additional|supporting|relevant documentation|document)\b", text):
        return "additional_document"
    if observation.page_type == "resume_upload":
        choice_index = _first_cover_letter_choice_index(observation)
        if choice_index is not None:
            if index < choice_index:
                return "resume"
            if index > choice_index:
                return "cover_letter"
        if field.required:
            return "resume"
        page_text = " ".join([observation.title, observation.visible_text, *observation.errors]).lower()
        if re.search(r"\bresume|resum[eé]|cv\b", page_text) and _count_upload_fields(observation) == 1:
            return "resume"
    return "unknown"


def _profile_fact(field: ObservedField, document_kind: str | None) -> str | None:
    if document_kind == "resume":
        return "resume_path"
    if document_kind == "cover_letter":
        return "cover_letter_path" if _is_file_upload_field(field) else "cover_letter"
    text = _combined_text(field)
    if "address line 2" in text or "address line two" in text:
        return None
    for fact, pattern in _PROFILE_FACT_PATTERNS:
        if re.search(pattern, text):
            return fact
    return None


def _sensitivity(field: ObservedField, profile_fact: str | None) -> FieldSensitivity:
    text = _combined_text(field)
    if profile_fact == "password" or re.search(r"\b(pass(word|code|phrase)?|otp|mfa|verification code)\b", text):
        return "credential"
    if re.search(
        r"\b(gender|sex|aboriginal|torres strait|ethnic|race|disability|veteran|indigenous|"
        r"criminal|police check|background check|health condition)\b",
        text,
    ):
        return "personal_sensitive"
    if re.search(r"\b(salary|compensation|visa|sponsor|right to work|work rights|declaration)\b", text):
        return "judgement"
    return "none"


def _answerability(
    field: ObservedField,
    *,
    label_quality: LabelQuality,
    document_kind: str | None,
    profile_fact: str | None,
    sensitivity: FieldSensitivity,
    profile_facts: dict[str, Any],
    approved_memory: list[dict[str, Any]],
    combined_text: str,
) -> tuple[FieldAnswerability, str]:
    if field.disabled or _field_has_useful_value(field):
        return "already_answered", "Field already has a useful value."
    if not field.required and document_kind == "additional_document":
        return "optional_skip", "Optional additional document upload has no approved file source."
    if profile_fact and _profile_values(profile_facts, profile_fact):
        return "profile", f"Field matches profile fact {profile_fact}."
    if _has_memory_answer(field, approved_memory):
        return "memory", "Field has an approved memory answer."
    if sensitivity in {"personal_sensitive", "credential"}:
        return "user_required", "Sensitive personal or credential field needs explicit user/profile source."
    if label_quality in {"missing", "weak"} and field.required:
        return "unsafe_unknown", "Required field label is missing or weak."
    if _CAREER_NARRATIVE.search(combined_text):
        return "inferable", "Career narrative can be drafted from job context and profile evidence."
    if not field.required:
        return "optional_skip", "Optional field has no approved deterministic source."
    return "user_required", "Required field has no deterministic profile or memory answer."


def _profile_values(profile_facts: dict[str, Any], fact: str) -> list[str]:
    paths = {
        "email": ["email", "contact.email", "external_accounts.default.email", "default.email"],
        "phone": ["phone", "contact.phone"],
        "linkedin_url": ["linkedin_url", "contact.linkedin", "contact.linkedin_url"],
        "full_name": ["name", "full_name"],
        "first_name": ["first_name"],
        "last_name": ["last_name"],
        "address.street": ["address.street", "address.formatted"],
        "address.suburb": ["address.suburb", "city", "location"],
        "address.postcode": ["address.postcode"],
        "address.state": ["address.state_code", "address.state"],
        "address.country": ["address.country"],
        "salutation": ["salutation", "external_accounts.default.salutation", "default.salutation"],
        "heard_about": ["heard_about", "external_accounts.default.heard_about", "default.heard_about"],
        "phone_device_type": [
            "phone_device_type",
            "external_accounts.default.phone_device_type",
            "default.phone_device_type",
        ],
        "working_rights": ["work_rights", "external_accounts.default.working_rights", "default.working_rights"],
        "password": ["password", "external_accounts.default.password", "default.password"],
        "resume_path": ["resume_path"],
        "cover_letter_path": ["cover_letter_path"],
        "cover_letter": ["cover_letter"],
    }.get(fact, [fact])
    values: list[str] = []
    for path in paths:
        current: Any = profile_facts
        for part in path.split("."):
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(part)
        if current not in (None, "", [], {}):
            values.append(str(current))
    return values


def _has_memory_answer(field: ObservedField, approved_memory: list[dict[str, Any]]) -> bool:
    label = _normalise(field.label)
    if not label:
        return False
    for item in approved_memory:
        question = _normalise(str(item.get("question", item.get("label", ""))))
        if question and (question in label or label in question) and (item.get("answer") or item.get("value")):
            return True
    return False


def _combined_text(field: ObservedField) -> str:
    return _normalise(" ".join([field.label, field.field_type, field.nearby_text, *field.options]))


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().strip("*").lower()


def _field_has_useful_value(field: ObservedField) -> bool:
    if field.invalid:
        return False
    value = (field.current_value or "").strip()
    if not value:
        return False
    if field.field_type == "select" and _normalise(value) in {
        "select",
        "select one",
        "choose",
        "choose one",
        "please select",
        "-- select an option --",
    }:
        return False
    return True


def _is_file_upload_field(field: ObservedField) -> bool:
    return field.field_type.strip().lower() == "file" or field.control_kind == "file_upload"


def _looks_like_noisy_script_or_job_text(label: str) -> bool:
    lowered = label.lower()
    return bool(
        "var " in lowered
        or "function(" in lowered
        or "regexinvalidfilenamecharacters" in lowered
        or (len(label) > 180 and re.search(r"\b(posted|closing date|job type|job category)\b", lowered))
    )


def _looks_like_control_transcript(text: str) -> bool:
    lowered = _normalise(text)
    if any(marker in lowered for marker in ("open list", "selected:", "setupconditionalattributeitems", "aattributeitems")):
        return True
    if len(text) < 180:
        return False
    option_words = len(re.findall(r"\b(select|choose|option|yes|no|none|basic|intermediate|proficient|fluent)\b", lowered))
    validation_words = re.search(r"\b(required|invalid|missing|must|cannot|failed|error|valid)\b", lowered)
    return option_words >= 4 and not validation_words


def _first_cover_letter_choice_index(observation: PageObservation) -> int | None:
    for index, field in enumerate(observation.fields):
        text = _combined_text(field)
        if field.field_type in {"radio", "select"} and "cover letter" in text:
            return index
    return None


def _count_upload_fields(observation: PageObservation) -> int:
    return sum(1 for field in observation.fields if _is_file_upload_field(field))
