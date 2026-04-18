"""LLM helpers for the profile interview workflow."""

from __future__ import annotations

import json
import logging

from openai import AsyncOpenAI

from app.settings import Settings
from app.state.canonical_profile import CanonicalEvidenceItem

log = logging.getLogger("profile_interview_ai")


def _build_client(settings: Settings) -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
    )


async def plan_profile_question(
    settings: Settings,
    *,
    item: CanonicalEvidenceItem,
    current_gap: str,
    asked_question_ids: list[str],
) -> dict[str, object]:
    client = _build_client(settings)
    system = (
        "You are a professional resume writer interviewing a candidate. "
        "Ask exactly one concise follow-up question that will best improve the missing STAR signal. "
        "Also provide a cautious example answer based only on the evidence already present, "
        "plus 1-3 short source-basis bullets and one practical coaching hint. "
        "Do not ask multiple questions. Do not invent facts or metrics. "
        "Return ONLY valid JSON with this shape: "
        "{\"question\": \"...\", \"suggested_answer\": \"...\", "
        "\"source_basis\": [\"...\"], \"improvement_hint\": \"...\"}."
    )
    user = f"""Evidence item:
source: {item.source}
role_title: {item.role_title or ""}
situation: {item.situation}
task: {item.task}
action: {item.action}
outcome: {item.outcome}
metrics: {item.metrics}
proof_points: {item.proof_points}

Current gap: {current_gap}
Previously asked question ids: {asked_question_ids}

Write the single best next question."""
    try:
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            max_tokens=180,
        )
        raw = response.choices[0].message.content or ""
        parsed = json.loads(raw)
        question = str(parsed.get("question", "")).strip()
        if question:
            return {
                "question": question,
                "suggested_answer": str(parsed.get("suggested_answer", "")).strip(),
                "source_basis": _normalize_source_basis(parsed.get("source_basis")),
                "improvement_hint": str(parsed.get("improvement_hint", "")).strip(),
            }
    except Exception as exc:  # pragma: no cover - network/runtime dependent
        log.warning("[plan_profile_question] falling back to deterministic prompt: %s", exc)
    return {
        "question": _fallback_question_for_gap(item, current_gap),
        "suggested_answer": _fallback_suggested_answer(item, current_gap),
        "source_basis": _fallback_source_basis(item),
        "improvement_hint": _fallback_improvement_hint(current_gap),
    }


async def interpret_profile_answer(
    settings: Settings,
    *,
    item: CanonicalEvidenceItem,
    current_gap: str,
    current_question: str,
    answer: str,
) -> dict[str, object]:
    client = _build_client(settings)
    system = (
        "You are helping normalize a candidate's rough answer into structured resume evidence. "
        "Map the answer only to the current gap. Do not invent facts. "
        "Return ONLY valid JSON with this shape: "
        "{\"field_updates\": {\"situation\": \"\", \"task\": \"\", \"outcome\": \"\", \"action\": \"\", \"metrics\": []}, "
        "\"approximate\": false, \"notes\": \"\"}. "
        "Only populate the field that the answer clearly supports."
    )
    user = f"""Current evidence item:
source: {item.source}
role_title: {item.role_title or ""}
situation: {item.situation}
task: {item.task}
action: {item.action}
outcome: {item.outcome}
metrics: {item.metrics}

Current gap: {current_gap}
Current question: {current_question}
Candidate answer: {answer}
"""
    try:
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            max_tokens=280,
        )
        raw = response.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        field_updates = parsed.get("field_updates", {})
        if isinstance(field_updates, dict):
            normalized_metrics = field_updates.get("metrics")
            if not isinstance(normalized_metrics, list):
                field_updates["metrics"] = []
            parsed["field_updates"] = field_updates
            return parsed
    except Exception as exc:  # pragma: no cover - network/runtime dependent
        log.warning("[interpret_profile_answer] falling back to deterministic interpretation: %s", exc)
    return _fallback_interpretation(current_gap, answer)


async def assess_profile_answer_quality(
    settings: Settings,
    *,
    item: CanonicalEvidenceItem,
    current_gap: str,
    current_question: str,
    answer: str,
) -> dict[str, object]:
    client = _build_client(settings)
    system = (
        "You are a professional resume writer evaluating the quality of one candidate answer. "
        "Score only the answer quality, not the candidate. Use a strict 0.0 to 1.0 scale. "
        "Assess specificity, ownership, outcome_strength, metric_usefulness, and groundedness. "
        "Return ONLY valid JSON with this shape: "
        "{\"score\": 0.0, "
        "\"dimension_scores\": {\"specificity\": 0.0, \"ownership\": 0.0, "
        "\"outcome_strength\": 0.0, \"metric_usefulness\": 0.0, \"groundedness\": 0.0}, "
        "\"strengths\": [\"...\"], \"weaknesses\": [\"...\"], "
        "\"next_focus\": \"...\", \"confidence\": \"low|medium|high\"}. "
        "Do not punish an answer for lacking a metric if the current gap is not metrics, but do mention it if useful."
    )
    user = f"""Evidence item:
source: {item.source}
role_title: {item.role_title or ""}
situation: {item.situation}
task: {item.task}
action: {item.action}
outcome: {item.outcome}
metrics: {item.metrics}

Current gap: {current_gap}
Current question: {current_question}
Candidate answer: {answer}
"""
    try:
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            max_tokens=260,
        )
        raw = response.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        return _normalize_assessment(parsed)
    except Exception as exc:  # pragma: no cover - network/runtime dependent
        log.warning("[assess_profile_answer_quality] falling back to deterministic scoring: %s", exc)
    return _fallback_answer_assessment(current_gap, answer)


def _fallback_question_for_gap(item: CanonicalEvidenceItem, gap: str) -> str:
    prompts = {
        "situation": f"What was the starting problem or context behind your {item.source} work?",
        "task": f"What were you personally responsible for owning in {item.source}?",
        "outcome": f"What changed or improved because of this {item.source} work?",
        "metrics": f"Do you have a number, scale, or measurable impact for this {item.source} work?",
    }
    return prompts.get(gap, f"What is the most important missing detail for this {item.source} example?")


def _fallback_suggested_answer(item: CanonicalEvidenceItem, gap: str) -> str:
    focus = _focus_phrase(item)
    if gap == "situation":
        return (
            f"Based on your resume, a draft answer could be: \"At {item.source}, the challenge was "
            f"finding a more reliable and scalable way to handle {focus}.\""
        )
    if gap == "task":
        return (
            f"Based on your resume, a draft answer could be: \"My role was to own the design and delivery "
            f"of the approach for {focus}.\""
        )
    if gap == "outcome":
        return (
            f"Based on your resume, a draft answer could be: \"The work improved delivery quality and "
            f"reduced manual effort around {focus}.\""
        )
    if gap == "metrics":
        return (
            "If you know the numbers, a grounded answer could mention time saved, number of systems, "
            "data volume, or the drop in manual effort. If you do not know an exact metric, say that honestly."
        )
    return f"Based on your resume, a draft answer could be: \"The important detail here was {focus}.\""


def _fallback_source_basis(item: CanonicalEvidenceItem) -> list[str]:
    basis: list[str] = []
    for candidate in [item.action, item.outcome, *item.proof_points, *item.metrics]:
        cleaned = str(candidate).strip()
        if cleaned and cleaned not in basis:
            basis.append(cleaned)
        if len(basis) == 3:
            break
    if basis:
        return basis
    return [f"{item.source} - {item.role_title or 'evidence item'}"]


def _fallback_improvement_hint(gap: str) -> str:
    hints = {
        "situation": "Add the pain point, constraint, or scale that made this problem worth solving.",
        "task": "Clarify what you personally owned versus what the team owned.",
        "outcome": "Say what changed for the business, users, or delivery process after your work landed.",
        "metrics": "Even an approximate range or scale marker is better than leaving this blank.",
    }
    return hints.get(gap, "Add one concrete detail that makes this example easier to picture.")


def _focus_phrase(item: CanonicalEvidenceItem) -> str:
    seed = next(
        (
            value.strip()
            for value in [item.action, *item.proof_points, item.outcome]
            if isinstance(value, str) and value.strip()
        ),
        "",
    )
    if not seed:
        return f"the work in your {item.source} example"

    normalized = seed.rstrip(".")
    prefixes = (
        "built ",
        "designed ",
        "delivered ",
        "created ",
        "implemented ",
        "developed ",
        "led ",
        "owned ",
    )
    lowered = normalized.lower()
    for prefix in prefixes:
        if lowered.startswith(prefix):
            return normalized[len(prefix) :]
    return normalized


def _normalize_source_basis(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for entry in value:
        cleaned = str(entry).strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result[:3]


def _fallback_interpretation(current_gap: str, answer: str) -> dict[str, object]:
    cleaned = answer.strip()
    if current_gap == "metrics":
        metrics = [line.strip() for line in cleaned.splitlines() if line.strip()]
        return {
            "field_updates": {"metrics": metrics},
            "approximate": False,
            "notes": "deterministic fallback",
        }
    return {
        "field_updates": {current_gap: cleaned, "metrics": []},
        "approximate": False,
        "notes": "deterministic fallback",
    }


def _normalize_assessment(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return _fallback_answer_assessment("", "")
    dimension_scores = value.get("dimension_scores", {})
    normalized_dimensions: dict[str, float] = {}
    if isinstance(dimension_scores, dict):
        for key in (
            "specificity",
            "ownership",
            "outcome_strength",
            "metric_usefulness",
            "groundedness",
        ):
            normalized_dimensions[key] = _clamp_score(dimension_scores.get(key))
    score = _clamp_score(value.get("score"))
    strengths = [str(entry).strip() for entry in value.get("strengths", []) if str(entry).strip()][:3] if isinstance(value.get("strengths"), list) else []
    weaknesses = [str(entry).strip() for entry in value.get("weaknesses", []) if str(entry).strip()][:3] if isinstance(value.get("weaknesses"), list) else []
    next_focus = str(value.get("next_focus", "")).strip()
    confidence = str(value.get("confidence", "draft")).strip() or "draft"
    return {
        "score": score,
        "dimension_scores": normalized_dimensions,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "next_focus": next_focus,
        "confidence": confidence,
    }


def _fallback_answer_assessment(current_gap: str, answer: str) -> dict[str, object]:
    cleaned = answer.strip()
    lowered = cleaned.lower()
    has_digits = any(char.isdigit() for char in cleaned)
    specificity = 0.35
    if len(cleaned.split()) >= 10:
        specificity += 0.15
    if has_digits or "," in cleaned:
        specificity += 0.1
    ownership = 0.65 if " i " in f" {lowered} " or lowered.startswith("i ") or " my " in f" {lowered} " else 0.4
    outcome_strength = 0.7 if any(token in lowered for token in ("improved", "reduced", "enabled", "increased", "faster", "simpler")) else 0.45
    metric_usefulness = 0.85 if has_digits else (0.55 if current_gap != "metrics" else 0.3)
    groundedness = 0.75 if len(cleaned.split()) >= 6 else 0.45
    dimensions = {
        "specificity": min(specificity, 0.9),
        "ownership": ownership,
        "outcome_strength": outcome_strength,
        "metric_usefulness": metric_usefulness,
        "groundedness": groundedness,
    }
    score = round(sum(dimensions.values()) / len(dimensions), 2)
    strengths: list[str] = []
    weaknesses: list[str] = []
    if dimensions["specificity"] >= 0.6:
        strengths.append("includes concrete context")
    else:
        weaknesses.append("still generic and could use more context")
    if dimensions["ownership"] >= 0.6:
        strengths.append("shows personal ownership")
    else:
        weaknesses.append("personal ownership is still a bit vague")
    if dimensions["metric_usefulness"] < 0.5:
        weaknesses.append("would be stronger with a metric or scale marker")
    next_focus = {
        "situation": "Add the pain point, constraint, or scale behind the problem.",
        "task": "Clarify exactly what you personally owned.",
        "outcome": "Tighten what changed for the business or delivery process.",
        "metrics": "Add a number, scale, or honest approximation if you can.",
    }.get(current_gap, "Add one more concrete detail to make the answer more usable.")
    return {
        "score": score,
        "dimension_scores": dimensions,
        "strengths": strengths[:3],
        "weaknesses": weaknesses[:3],
        "next_focus": next_focus,
        "confidence": "low",
    }


def _clamp_score(value: object) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, round(numeric, 2)))
