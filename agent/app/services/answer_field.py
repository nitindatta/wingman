"""AI field proposer — maps form fields to values using profile, cache, and LLM.

Resolution order:
  1. Profile lookup (name, email, phone, work rights, location)
  2. Cache lookup (question_answer_cache table — keyword/token overlap match)
  3. Batched LLM call (only for fields not resolved above)
  4. Pause (interrupt) if LLM confidence is low
"""

from __future__ import annotations

import json
import logging

from openai import AsyncOpenAI

from app.persistence.sqlite.question_cache import SqliteQuestionCacheRepository
from app.settings import Settings
from app.state.apply import FieldInfo

log = logging.getLogger("answer_field")


# ── 1. Profile lookup ──────────────────────────────────────────────────────

_PROFILE_FIELD_MAP = {
    # Common SEEK field labels → profile keys
    "first name": lambda p: p.get("name", "").split()[0] if p.get("name") else "",
    "last name": lambda p: p.get("name", "").split()[-1] if p.get("name") else "",
    "full name": lambda p: p.get("name", ""),
    "name": lambda p: p.get("name", ""),
    "email": lambda p: p.get("contact", {}).get("email", ""),
    "email address": lambda p: p.get("contact", {}).get("email", ""),
    "phone": lambda p: p.get("contact", {}).get("phone", ""),
    "phone number": lambda p: p.get("contact", {}).get("phone", ""),
    "mobile": lambda p: p.get("contact", {}).get("phone", ""),
    "location": lambda p: p.get("location", ""),
    "city": lambda p: p.get("location", "").split(",")[0].strip() if p.get("location") else "",
    "right to work": lambda p: p.get("work_rights", ""),
    "work rights": lambda p: p.get("work_rights", ""),
    "right to work in australia": lambda p: p.get("work_rights", ""),
    "salary": lambda p: p.get("salary_expectation", ""),
    "salary expectation": lambda p: p.get("salary_expectation", ""),
    "expected salary": lambda p: p.get("salary_expectation", ""),
    "notice period": lambda p: p.get("notice_period", ""),
    "availability": lambda p: p.get("notice_period", ""),
    "cover letter": None,  # handled separately
}


def _skills_set(profile: dict) -> set[str]:
    """All skill names from profile in lowercase for fast lookup."""
    skills = profile.get("core_strengths", [])
    # Also pull from experience highlights for broader coverage
    extra = []
    for exp in profile.get("experience", []):
        extra.extend(exp.get("technologies", []))
        extra.extend(exp.get("skills", []))
    return {s.lower() for s in skills + extra}


def _best_select_match(value: str, options: list[str]) -> str | None:
    """Find the best matching option for a profile value in a select field.

    Tries in order:
    1. Exact case-insensitive match
    2. Any option that contains the profile value as a substring (or vice-versa)
    3. Numeric range overlap — extract all integers from both sides and check if
       the profile's lower-bound falls inside the option's range. Handles the
       common SEEK +1 offset ($180,000 profile → "$180,001 - $200,000" option).
    """
    import re as _re

    val_lower = value.lower().strip()

    # 1. Exact
    for opt in options:
        if opt.lower().strip() == val_lower:
            return opt

    # 2. Substring containment
    for opt in options:
        opt_l = opt.lower()
        if val_lower in opt_l or opt_l in val_lower:
            return opt

    # 3. Numeric range overlap
    val_nums = [int(n.replace(",", "")) for n in _re.findall(r"[\d,]+", value) if n.replace(",", "").isdigit()]
    if val_nums:
        val_lo = val_nums[0]
        val_hi = val_nums[-1] if len(val_nums) > 1 else val_lo
        best_opt = None
        best_dist = float("inf")
        for opt in options:
            opt_nums = [int(n.replace(",", "")) for n in _re.findall(r"[\d,]+", opt) if n.replace(",", "").isdigit()]
            if len(opt_nums) >= 2:
                opt_lo, opt_hi = opt_nums[0], opt_nums[-1]
                # Check if our range overlaps with this option's range
                if opt_lo <= val_hi and opt_hi >= val_lo:
                    dist = abs(opt_lo - val_lo)
                    if dist < best_dist:
                        best_dist = dist
                        best_opt = opt
            elif len(opt_nums) == 1:
                dist = abs(opt_nums[0] - val_lo)
                if dist < best_dist:
                    best_dist = dist
                    best_opt = opt
        if best_opt and best_dist < 10_000:
            return best_opt

    return None


def _raw_profile_value(field: FieldInfo, profile: dict) -> str | None:
    """Return the raw profile value for a field label, without any select validation."""
    label_lower = field.label.lower().strip()
    for key, resolver in _PROFILE_FIELD_MAP.items():
        if key in label_lower and resolver is not None:
            value = resolver(profile)
            return value if value else None
    return None


def _lookup_from_profile(field: FieldInfo, profile: dict) -> str | None:
    label_lower = field.label.lower().strip()
    for key, resolver in _PROFILE_FIELD_MAP.items():
        if key in label_lower and resolver is not None:
            value = resolver(profile)
            if not value:
                return None
            # For select fields, find the closest matching option.
            if field.field_type == "select" and field.options:
                matched = _best_select_match(value, field.options)
                if matched:
                    log.debug("[profile] select matched %r → %r for label=%r", value, matched, field.label)
                    return matched
                log.debug("[profile] select value %r no match in options for label=%r — falling through", value, field.label)
                return None
            return value
    return None


# ── 2. Cache lookup ────────────────────────────────────────────────────────

async def _lookup_from_cache(
    field: FieldInfo, cache: SqliteQuestionCacheRepository | None
) -> str | None:
    """Look up a previously approved answer by token overlap."""
    if cache is None:
        return None
    return await cache.find(field.label)


async def _save_to_cache(
    field: FieldInfo,
    answer: str,
    cache: SqliteQuestionCacheRepository | None,
    *,
    source: str,
) -> None:
    if cache is None or not field.label:
        return
    await cache.save(field.label, answer, field_type=field.field_type, source=source)


# ── 3. LLM call ────────────────────────────────────────────────────────────

async def _resolve_via_llm(
    field: FieldInfo,
    profile: dict,
    settings: Settings,
    cover_letter: str,
    profile_hint: str | None = None,
) -> tuple[str, float]:
    """Returns (answer, confidence) where confidence is 0.0–1.0."""
    client = AsyncOpenAI(base_url=settings.openai_base_url, api_key=settings.openai_api_key)

    options_text = ""
    if field.options:
        options_list = "\n".join(f"  - {o}" for o in field.options)
        options_text = f"\nOptions:\n{options_list}"

    is_radio_group = field.field_type == "radio" and bool(field.options)
    radio_instruction = (
        "This is a radio button group. You must pick EXACTLY ONE option from the list above. "
        "Return the exact text of the chosen option as the answer. "
        "Pick the option most appropriate for the candidate."
    ) if is_radio_group else ""

    # Build a richer profile block so the LLM can answer screening questions accurately
    exp_lines = []
    for exp in profile.get("experience", [])[:4]:
        techs = exp.get("technologies", []) + exp.get("skills", [])
        line = f"- {exp.get('title','')} at {exp.get('company','')} ({exp.get('period','')})"
        if techs:
            line += f": {', '.join(techs[:8])}"
        exp_lines.append(line)
    experience_block = "\n".join(exp_lines) or "Not provided"

    skills_block = ", ".join(profile.get("core_strengths", []))
    narrative_block = "\n".join(
        f"- {s}" for s in profile.get("narrative_strengths", [])[:5]
    )

    system = (
        "You are filling out a job application form on behalf of a candidate. "
        "Answer each question accurately based solely on the candidate's profile below. "
        "For yes/no or radio questions: pick the option that is most truthful given the candidate's experience. "
        "If the candidate clearly does NOT have the stated experience, answer 'No'. "
        "Set confidence < 0.6 if you are genuinely unsure. "
        "Return JSON only: {\"answer\": \"...\", \"confidence\": 0.0-1.0}"
    )
    user = f"""Form field: {field.label}
Field type: {field.field_type}{options_text}
Required: {field.required}
{radio_instruction}

Candidate profile:
Name: {profile.get('name')}
Location: {profile.get('location')}
Summary: {profile.get('summary', '')[:300]}

Experience:
{experience_block}

Skills: {skills_block}

Key achievements:
{narrative_block}

Cover letter excerpt: {cover_letter[:400] if cover_letter else 'N/A'}
{f"Candidate's stated preference for this field: {profile_hint}" if profile_hint else ""}
Answer this field truthfully for the candidate. Return only JSON."""

    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
        max_tokens=200,
        _call_name=f"answer_field single: {field.label[:60]}",
    )
    raw = response.choices[0].message.content or "{}"
    try:
        parsed = json.loads(raw)
        return str(parsed.get("answer", "")), float(parsed.get("confidence", 0.5))
    except (json.JSONDecodeError, ValueError):
        return raw.strip(), 0.3


# ── Public API ─────────────────────────────────────────────────────────────

LOW_CONFIDENCE_THRESHOLD = 0.6


async def _resolve_batch_via_llm(
    fields_with_hints: list[tuple[FieldInfo, str | None]],
    profile: dict,
    settings: Settings,
    cover_letter: str,
) -> dict[str, tuple[str, float]]:
    """Resolve multiple fields in one LLM request."""
    if not fields_with_hints:
        return {}

    client = AsyncOpenAI(base_url=settings.openai_base_url, api_key=settings.openai_api_key)

    exp_lines = []
    for exp in profile.get("experience", [])[:4]:
        techs = exp.get("technologies", []) + exp.get("skills", [])
        line = f"- {exp.get('title','')} at {exp.get('company','')} ({exp.get('period','')})"
        if techs:
            line += f": {', '.join(techs[:8])}"
        exp_lines.append(line)
    experience_block = "\n".join(exp_lines) or "Not provided"
    skills_block = ", ".join(profile.get("core_strengths", []))
    narrative_block = "\n".join(
        f"- {s}" for s in profile.get("narrative_strengths", [])[:5]
    )
    fields_payload = [
        {
            "id": field.id,
            "label": field.label,
            "field_type": field.field_type,
            "required": field.required,
            "options": field.options or [],
            "current_value": field.current_value,
            "profile_hint": profile_hint,
        }
        for field, profile_hint in fields_with_hints
    ]

    system = (
        "You are filling out a job application form on behalf of a candidate. "
        "Answer each question accurately based solely on the candidate's profile below. "
        "For yes/no or radio questions: pick the option that is most truthful given the candidate's experience. "
        "If the candidate clearly does NOT have the stated experience, answer 'No'. "
        "Set confidence < 0.6 if you are genuinely unsure. "
        "Return JSON only: {\"answers\": [{\"id\": \"field-id\", \"answer\": \"...\", \"confidence\": 0.0-1.0}]}. "
        "Return exactly one answer object for every requested field id."
    )
    user = f"""Form fields:
{json.dumps(fields_payload, ensure_ascii=False, indent=2)}

Candidate profile:
Name: {profile.get('name')}
Location: {profile.get('location')}
Summary: {profile.get('summary', '')[:300]}

Experience:
{experience_block}

Skills: {skills_block}

Key achievements:
{narrative_block}

Cover letter excerpt: {cover_letter[:400] if cover_letter else 'N/A'}
Answer every field truthfully for the candidate. For select/radio fields, use the exact option text when possible. Return only JSON."""

    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
        max_tokens=min(2500, max(400, len(fields_with_hints) * 160)),
        _call_name=f"answer_field batch: {len(fields_with_hints)} fields",
    )
    raw = response.choices[0].message.content or "{}"
    requested_ids = {field.id for field, _ in fields_with_hints}
    try:
        parsed = json.loads(raw)
        rows = parsed.get("answers", parsed if isinstance(parsed, list) else [])
        if isinstance(rows, dict):
            rows = [
                {"id": field_id, **payload}
                for field_id, payload in rows.items()
                if isinstance(payload, dict)
            ]

        results: dict[str, tuple[str, float]] = {}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            field_id = str(row.get("id", ""))
            if field_id not in requested_ids:
                continue
            answer = str(row.get("answer", ""))
            try:
                confidence = float(row.get("confidence", 0.5))
            except (TypeError, ValueError):
                confidence = 0.5
            results[field_id] = (answer, confidence)

        for field, _ in fields_with_hints:
            results.setdefault(field.id, ("", 0.3))
        return results
    except (json.JSONDecodeError, ValueError):
        return {field.id: (raw.strip(), 0.3) for field, _ in fields_with_hints}


def _is_blank(value: str) -> bool:
    return value.strip() == ""


def _requires_nonblank(field: FieldInfo) -> bool:
    return field.required or field.field_type == "select"


def _record_missing_required(
    field: FieldInfo,
    proposed: dict[str, str],
    low_confidence: list[str],
    source: str,
) -> bool:
    if _requires_nonblank(field) and _is_blank(proposed.get(field.id, "")):
        if field.id not in low_confidence:
            low_confidence.append(field.id)
        log.info("[field:%s] label=%r needs review: %s returned blank required value", field.id, field.label, source)
        return True
    return False


async def propose_field_values(
    fields: list[FieldInfo],
    profile: dict,
    cover_letter: str,
    settings: Settings,
    question_cache: SqliteQuestionCacheRepository | None = None,
) -> tuple[dict[str, str], list[str]]:
    """
    Returns (proposed_values, low_confidence_ids).

    proposed_values: {field_id: proposed_value} for all fields
    low_confidence_ids: field ids where LLM confidence < threshold → trigger interrupt
    """
    proposed: dict[str, str] = {}
    low_confidence: list[str] = []
    llm_fields: list[tuple[FieldInfo, str | None]] = []

    log.info("[propose_field_values] resolving %d fields", len(fields))

    for field in fields:
        if field.field_type == "file":
            log.debug("[field:%s] type=file — skipped", field.id)
            continue

        label_lower = field.label.lower()

        # Cover letter textarea
        if "cover letter" in label_lower and field.field_type == "textarea":
            proposed[field.id] = cover_letter
            log.debug("[field:%s] label=%r → cover_letter (%d words)", field.id, field.label, len(cover_letter.split()))
            continue

        # Resume / CV selector — always pick current value or first option; never send to LLM
        _resume_labels = ("resum", "resumé", "cv", "curriculum vitae", "upload resume", "select resume")
        if any(kw in label_lower for kw in _resume_labels):
            if field.current_value:
                proposed[field.id] = field.current_value
                log.debug("[field:%s] label=%r → resume keep current=%r", field.id, field.label, field.current_value)
            elif field.options:
                proposed[field.id] = field.options[0]
                log.debug("[field:%s] label=%r → resume pick first option=%r", field.id, field.label, field.options[0])
            else:
                log.debug("[field:%s] label=%r → resume no value/options, skipping", field.id, field.label)
            continue

        # Radio groups: cover letter → force "Write a cover letter"
        if field.field_type == "radio":
            if "cover letter" in label_lower and field.options:
                write_opt = next((o for o in field.options if "write" in o.lower()), None)
                if write_opt:
                    proposed[field.id] = write_opt
                    log.debug("[field:%s] label=%r → radio force=%r", field.id, field.label, write_opt)
                    continue
            # If already has a pre-selected value, keep it (SEEK sometimes pre-fills)
            if field.current_value:
                proposed[field.id] = field.current_value
                log.debug("[field:%s] label=%r → radio keep default=%r", field.id, field.label, field.current_value)
                continue
            # No default — screening question, must answer via LLM (fall through below)

        # 1. Profile lookup
        value = _lookup_from_profile(field, profile)
        if value is not None:
            proposed[field.id] = value
            log.debug("[field:%s] label=%r → profile value=%r", field.id, field.label, value)
            continue

        # Capture the raw profile preference so the LLM can use it as a hint if we fall through.
        # (e.g. profile says "$180,000-$200,000" but select options don't match exactly)
        profile_hint = _raw_profile_value(field, profile)

        # 2. Cache lookup
        value = await _lookup_from_cache(field, question_cache)
        if value is not None:
            proposed[field.id] = value
            if _record_missing_required(field, proposed, low_confidence, "cache"):
                continue
            log.debug("[field:%s] label=%r → cache value=%r", field.id, field.label, value)
            continue

        # 2b. Skill checkbox — resolve Yes/No directly from profile skills (no LLM)
        if field.field_type == "checkbox":
            known = _skills_set(profile)
            answer = "Yes" if label_lower.strip() in known else "No"
            proposed[field.id] = answer
            log.debug("[field:%s] label=%r → skill_check=%r", field.id, field.label, answer)
            continue

        # 3. LLM — pass the profile hint so it doesn't guess freely
        llm_fields.append((field, profile_hint))
    if llm_fields:
        log.info("[propose_field_values] resolving %d fields via one LLM batch", len(llm_fields))
        llm_results = await _resolve_batch_via_llm(
            llm_fields,
            profile=profile,
            settings=settings,
            cover_letter=cover_letter,
        )
        for field, _profile_hint in llm_fields:
            value, confidence = llm_results.get(field.id, ("", 0.3))
            proposed[field.id] = value
            if _record_missing_required(field, proposed, low_confidence, "LLM"):
                continue
            if confidence >= LOW_CONFIDENCE_THRESHOLD:
                await _save_to_cache(field, value, question_cache, source="llm")
            if confidence < LOW_CONFIDENCE_THRESHOLD:
                low_confidence.append(field.id)
                log.info("[field:%s] label=%r → LLM LOW_CONF=%.2f value=%r", field.id, field.label, confidence, value)
            else:
                log.debug("[field:%s] label=%r → LLM conf=%.2f value=%r", field.id, field.label, confidence, value)

    log.info("[propose_field_values] done: proposed=%d low_confidence=%s", len(proposed), low_confidence)
    return proposed, low_confidence
