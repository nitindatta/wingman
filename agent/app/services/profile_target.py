"""Build a canonical, STAR-friendly profile draft from raw profile data."""

from __future__ import annotations

import re
from typing import Any

from app.state.canonical_profile import (
    CanonicalEvidenceItem,
    CanonicalProfile,
    ProfileAnswer,
    ProfileEnrichmentQuestion,
)
from app.state.raw_profile import RawProfile
from app.services.voice_profile import build_voice_profile_sync

_DOMAIN_RULES: dict[str, tuple[str, ...]] = {
    "education": ("education", "student", "school"),
    "financial services": ("bank", "credit union", "financial", "warehouse"),
    "data platform": (
        "databricks",
        "spark",
        "dbt",
        "redshift",
        "pipeline",
        "ingestion",
        "data platform",
        "entity resolution",
    ),
    "AI": ("llm", "embedding", "agent", "semantic", "ai "),
    "cloud": ("aws", "azure", "lambda", "kinesis", "glue", "s3"),
}


def build_canonical_profile(raw_profile: dict[str, Any]) -> CanonicalProfile:
    core_strengths = _clean_list(raw_profile.get("core_strengths", []))
    writing_samples = _clean_list(raw_profile.get("writing_samples", []))
    narrative_strengths = _clean_list(raw_profile.get("narrative_strengths", []))

    evidence_items: list[CanonicalEvidenceItem] = []

    for exp in raw_profile.get("experience", []):
        if not isinstance(exp, dict):
            continue
        item = _build_experience_item(exp, core_strengths, narrative_strengths, writing_samples)
        if item is not None:
            evidence_items.append(item)

    for project in raw_profile.get("selected_projects", []):
        if not isinstance(project, dict):
            continue
        item = _build_project_item(project, core_strengths, writing_samples)
        if item is not None:
            evidence_items.append(item)

    return CanonicalProfile(
        name=str(raw_profile.get("name", "")).strip(),
        headline=str(raw_profile.get("headline", "")).strip(),
        summary=str(raw_profile.get("summary", "")).strip(),
        location=_optional_text(raw_profile.get("location")),
        work_rights=_optional_text(raw_profile.get("work_rights")),
        salary_expectation=_optional_text(raw_profile.get("salary_expectation")),
        core_strengths=core_strengths,
        voice_samples=writing_samples,
        voice_profile=build_voice_profile_sync(writing_samples),
        evidence_items=evidence_items,
    )


def build_profile_enrichment_questions(
    profile: CanonicalProfile,
    *,
    limit: int = 12,
) -> list[ProfileEnrichmentQuestion]:
    questions: list[ProfileEnrichmentQuestion] = []

    if len(profile.voice_samples) < 3:
        questions.append(
            ProfileEnrichmentQuestion(
                id="voice-samples",
                target_field="voice_samples",
                prompt="Add 2 or 3 short lines that sound naturally like you when you describe your work.",
                help_text="These are used for voice only, not factual evidence.",
                priority="high",
                input_type="textarea",
                current_value=_current_value_for_target_field(profile, "voice_samples"),
            )
        )

    if not profile.summary.strip():
        questions.append(
            ProfileEnrichmentQuestion(
                id="summary",
                target_field="summary",
                prompt="Write a 2 to 3 sentence summary of the kind of work you want to be hired for now.",
                help_text="Keep it factual and current, not a full career history.",
                priority="medium",
                input_type="textarea",
                current_value=_current_value_for_target_field(profile, "summary"),
            )
        )

    for item in profile.evidence_items[:6]:
        base = f"evidence_items[{item.id}]"
        if not item.situation.strip():
            questions.append(
                ProfileEnrichmentQuestion(
                    id=f"{item.id}-situation",
                    evidence_item_id=item.id,
                    target_field=f"{base}.situation",
                    prompt=f"What was the starting situation or problem behind your {item.source} work as {item.role_title or 'this role'}?",
                    help_text="Describe the context before you stepped in.",
                    priority="high",
                    input_type="textarea",
                    current_value=_current_value_for_target_field(profile, f"{base}.situation"),
                )
            )
        if not item.task.strip():
            questions.append(
                ProfileEnrichmentQuestion(
                    id=f"{item.id}-task",
                    evidence_item_id=item.id,
                    target_field=f"{base}.task",
                    prompt=f"What were you specifically responsible for owning or delivering in {item.source}?",
                    help_text="This should describe your part of the work, not the whole team.",
                    priority="medium",
                    input_type="textarea",
                    current_value=_current_value_for_target_field(profile, f"{base}.task"),
                )
            )
        if not item.outcome.strip():
            questions.append(
                ProfileEnrichmentQuestion(
                    id=f"{item.id}-outcome",
                    evidence_item_id=item.id,
                    target_field=f"{base}.outcome",
                    prompt=f"What changed or improved because of this {item.source} work?",
                    help_text="Prefer a business or operational result, not just a technical activity.",
                    priority="high",
                    input_type="textarea",
                    current_value=_current_value_for_target_field(profile, f"{base}.outcome"),
                )
            )
        if not item.metrics:
            questions.append(
                ProfileEnrichmentQuestion(
                    id=f"{item.id}-metrics",
                    evidence_item_id=item.id,
                    target_field=f"{base}.metrics",
                    prompt=f"Do you have a number, scale, volume, latency, cost, or time impact for this {item.source} work?",
                    help_text="Even an approximate range is more useful than nothing.",
                    priority="medium",
                    input_type="textarea",
                    current_value=_current_value_for_target_field(profile, f"{base}.metrics"),
                )
            )

    return questions[:limit]


def build_canonical_profile_from_raw_profile(raw_profile: RawProfile) -> CanonicalProfile:
    evidence_items: list[CanonicalEvidenceItem] = []

    for exp in raw_profile.experience:
        bullet_texts = [bullet.text for bullet in exp.bullets]
        evidence_items.append(
            CanonicalEvidenceItem(
                id=exp.id,
                source=exp.company or exp.title or "Experience",
                role_title=exp.title or None,
                skills=exp.technologies[:6],
                domain=_infer_domains("\n".join([exp.company, exp.title, *bullet_texts, *exp.metrics])),
                action=bullet_texts[0] if bullet_texts else "",
                metrics=exp.metrics[:3],
                proof_points=bullet_texts[:4],
                confidence="draft",
            )
        )

    for project in raw_profile.projects:
        bullet_texts = [bullet.text for bullet in project.bullets]
        evidence_items.append(
            CanonicalEvidenceItem(
                id=project.id,
                source=project.name or "Project",
                role_title="Project",
                skills=project.technologies[:6],
                domain=_infer_domains("\n".join([project.name, project.summary, *bullet_texts])),
                action=project.summary or (bullet_texts[0] if bullet_texts else ""),
                proof_points=bullet_texts[:4] or ([project.summary] if project.summary else []),
                confidence="draft",
            )
        )

    return CanonicalProfile(
        name=raw_profile.identity.name,
        headline=raw_profile.identity.headline,
        summary=raw_profile.summary,
        location=raw_profile.identity.location or None,
        core_strengths=raw_profile.skills,
        voice_samples=raw_profile.writing_samples,
        voice_profile=build_voice_profile_sync(raw_profile.writing_samples),
        evidence_items=evidence_items,
    )


def apply_profile_answers(
    profile: CanonicalProfile,
    answers: list[ProfileAnswer],
) -> CanonicalProfile:
    updated = profile.model_copy(deep=True)
    evidence_by_id = {item.id: item for item in updated.evidence_items}

    for answer in answers:
        target_field = answer.target_field.strip()
        if not target_field:
            continue
        if target_field == "summary":
            updated.summary = answer.value.strip()
            continue
        if target_field == "voice_samples":
            updated.voice_samples = _split_multiline_values(answer.value)
            updated.voice_profile = build_voice_profile_sync(updated.voice_samples)
            continue

        match = re.fullmatch(r"evidence_items\[([^\]]+)\]\.(\w+)", target_field)
        if not match:
            continue
        item_id, field_name = match.groups()
        item = evidence_by_id.get(item_id)
        if item is None:
            continue
        if field_name in {"situation", "task", "action", "outcome"}:
            setattr(item, field_name, answer.value.strip())
        elif field_name == "metrics":
            item.metrics = _split_multiline_values(answer.value)

    if updated.voice_samples and not updated.voice_profile.tone_labels:
        updated.voice_profile = build_voice_profile_sync(updated.voice_samples)

    return updated


def extract_voice_samples_from_answer(answer: str, *, limit: int = 2) -> list[str]:
    if not answer.strip():
        return []

    normalized = answer.replace("\r\n", "\n").strip()
    segments = re.split(r"\n+|(?<=[.!?])\s+", normalized)
    samples: list[str] = []
    seen: set[str] = set()

    for segment in segments:
        candidate = " ".join(segment.strip().strip("\"'").split())
        if not candidate:
            continue
        if not _looks_like_voice_sample(candidate):
            continue
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        samples.append(candidate)
        if len(samples) >= limit:
            break

    return samples


def merge_voice_samples(existing: list[str], answer: str, *, max_samples: int = 12) -> list[str]:
    merged = _unique_preserving_order([*existing, *extract_voice_samples_from_answer(answer)])
    if len(merged) <= max_samples:
        return merged
    return merged[-max_samples:]


def _build_experience_item(
    exp: dict[str, Any],
    core_strengths: list[str],
    narrative_strengths: list[str],
    writing_samples: list[str],
) -> CanonicalEvidenceItem | None:
    company = str(exp.get("company", "")).strip()
    title = str(exp.get("title", "")).strip()
    if not company and not title:
        return None

    highlights = _clean_list(exp.get("highlights", []))
    metrics = _clean_list(exp.get("metrics", []))
    narrative = _find_narrative_match(company, title, narrative_strengths)
    text_parts = [company, title, narrative, *highlights, *metrics]
    combined_text = "\n".join(part for part in text_parts if part)

    proof_points = _unique_preserving_order([narrative, *highlights])[:4]
    action = narrative or (highlights[0] if highlights else "")

    return CanonicalEvidenceItem(
        id=_slug(f"{company}-{title}") or _slug(company) or "experience-item",
        source=company or title or "Experience",
        role_title=title or None,
        skills=_infer_skills(combined_text, core_strengths),
        domain=_infer_domains(combined_text),
        action=action,
        metrics=metrics[:3],
        proof_points=proof_points,
        tone_sample=_pick_tone_sample(writing_samples, combined_text),
    )


def _build_project_item(
    project: dict[str, Any],
    core_strengths: list[str],
    writing_samples: list[str],
) -> CanonicalEvidenceItem | None:
    name = str(project.get("name", "")).strip()
    summary = str(project.get("summary", "")).strip()
    if not name and not summary:
        return None

    combined_text = "\n".join(part for part in [name, summary] if part)
    return CanonicalEvidenceItem(
        id=_slug(name) or "project-item",
        source=name or "Project",
        role_title="Project",
        skills=_infer_skills(combined_text, core_strengths),
        domain=_infer_domains(combined_text),
        action=summary,
        proof_points=[summary] if summary else [],
        tone_sample=_pick_tone_sample(writing_samples, combined_text),
    )


def _find_narrative_match(company: str, title: str, narrative_strengths: list[str]) -> str:
    company_key = company.strip().lower()
    title_key = title.strip().lower()
    for item in narrative_strengths:
        lowered = item.lower()
        if company_key and company_key in lowered:
            return item
        if title_key and title_key in lowered:
            return item
    return ""


def _infer_skills(text: str, core_strengths: list[str]) -> list[str]:
    lowered = text.lower()
    matches = [skill for skill in core_strengths if skill.lower() in lowered]
    return matches[:6]


def _infer_domains(text: str) -> list[str]:
    lowered = text.lower()
    domains = [
        name
        for name, keywords in _DOMAIN_RULES.items()
        if any(keyword in lowered for keyword in keywords)
    ]
    return domains[:4]


def _pick_tone_sample(samples: list[str], text: str) -> str | None:
    if not samples:
        return None
    lowered = text.lower()
    for sample in samples:
        tokens = [token for token in re.split(r"[^a-z0-9]+", sample.lower()) if len(token) > 4]
        if any(token in lowered for token in tokens):
            return sample
    return samples[0]


def _clean_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    return _unique_preserving_order(cleaned)


def _unique_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _current_value_for_target_field(profile: CanonicalProfile, target_field: str) -> str | None:
    if target_field == "summary":
        return profile.summary.strip() or None
    if target_field == "voice_samples":
        return "\n".join(profile.voice_samples).strip() or None

    match = re.fullmatch(r"evidence_items\[([^\]]+)\]\.(\w+)", target_field)
    if not match:
        return None

    item_id, field_name = match.groups()
    item = next((evidence for evidence in profile.evidence_items if evidence.id == item_id), None)
    if item is None:
        return None
    if field_name == "metrics":
        return "\n".join(item.metrics).strip() or None
    value = getattr(item, field_name, None)
    if isinstance(value, str):
        return value.strip() or None
    return None


def _split_multiline_values(value: str) -> list[str]:
    values = [line.strip() for line in value.splitlines() if line.strip()]
    return _unique_preserving_order(values)


def _looks_like_voice_sample(value: str) -> bool:
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", value)
    if len(words) < 6 or len(words) > 35:
        return False
    if re.fullmatch(r"[\d\s,./:%()+-]+", value):
        return False
    lowercase_words = [word.lower() for word in words]
    stopword_hits = sum(
        1
        for word in lowercase_words
        if word
        in {
            "i",
            "my",
            "me",
            "the",
            "a",
            "an",
            "to",
            "and",
            "for",
            "with",
            "that",
            "because",
            "when",
            "while",
            "by",
        }
    )
    return stopword_hits >= 2


def _slug(value: str) -> str:
    lowered = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return lowered[:80]
