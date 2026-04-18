"""Build a lightweight writing-style profile from user-authored samples."""

from __future__ import annotations

import json
import logging
import re

from openai import AsyncOpenAI

from app.settings import Settings
from app.state.canonical_profile import VoiceProfile

log = logging.getLogger("voice_profile")

_ALLOWED_TONE_LABELS = {
    "direct",
    "practical",
    "grounded",
    "reflective",
    "technical",
    "collaborative",
    "concise",
}
_ALLOWED_FORMALITY = {"conversational", "semi-formal", "formal"}
_ALLOWED_SENTENCE_STYLES = {"short", "short_to_medium", "medium", "long"}
_ALLOWED_OPENING_STYLES = {"problem_first", "context_first", "action_first", "first_person_direct"}


def _build_client(settings: Settings) -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
    )


async def build_voice_profile(
    samples: list[str],
    *,
    settings: Settings | None = None,
) -> VoiceProfile:
    cleaned = _clean_samples(samples)
    baseline = build_voice_profile_sync(cleaned)

    if settings is None or len(cleaned) < 3:
        return baseline

    try:
        refined = await _refine_with_llm(settings, cleaned, baseline)
    except Exception as exc:  # pragma: no cover - network/runtime dependent
        log.warning("[build_voice_profile] falling back to deterministic analysis: %s", exc)
        return baseline
    return _merge_voice_profiles(baseline, refined)


def build_voice_profile_sync(samples: list[str]) -> VoiceProfile:
    cleaned = _clean_samples(samples)
    return _build_deterministic_voice_profile(cleaned)


def _build_deterministic_voice_profile(samples: list[str]) -> VoiceProfile:
    if not samples:
        return VoiceProfile()

    avg_words = sum(len(re.findall(r"[A-Za-z][A-Za-z'-]*", sample)) for sample in samples) / len(samples)
    uses_contractions = any(re.search(r"\b\w+'\w+\b", sample) for sample in samples)
    first_person_hits = sum(
        len(re.findall(r"\b(i|i'm|i've|i'd|my|me|mine)\b", sample.lower()))
        for sample in samples
    )
    prefers_first_person = first_person_hits > 0

    tone_labels: list[str] = []
    if avg_words <= 18:
        tone_labels.append("concise")
    if avg_words <= 24:
        tone_labels.append("direct")
    else:
        tone_labels.append("reflective")

    lowered_samples = [sample.lower() for sample in samples]
    combined = "\n".join(lowered_samples)
    if any(word in combined for word in ("practical", "ship", "delivery", "production", "usable", "operate")):
        tone_labels.append("practical")
    if any(word in combined for word in ("databricks", "pipeline", "system", "platform", "schema", "llm", "api")):
        tone_labels.append("technical")
    if any(word in combined for word in ("team", "stakeholder", "we ", "partner", "collaborat")):
        tone_labels.append("collaborative")
    if any(word in combined for word in ("tend", "prefer", "usually", "when i", "i like")):
        tone_labels.append("reflective")
    if not tone_labels:
        tone_labels.append("grounded")
    elif "grounded" not in tone_labels:
        tone_labels.append("grounded")

    if uses_contractions and avg_words <= 24:
        formality = "semi-formal"
    elif uses_contractions:
        formality = "conversational"
    else:
        formality = "formal" if avg_words > 24 else "semi-formal"

    if avg_words <= 10:
        sentence_style = "short"
    elif avg_words <= 20:
        sentence_style = "short_to_medium"
    elif avg_words <= 30:
        sentence_style = "medium"
    else:
        sentence_style = "long"

    opening_style = _infer_opening_style(samples)
    strengths = _infer_strengths(tone_labels)
    avoid = ["generic enthusiasm", "marketing buzzwords"]
    confidence = "medium" if len(samples) >= 3 else "low"

    return VoiceProfile(
        tone_labels=_unique_preserving_order([label for label in tone_labels if label in _ALLOWED_TONE_LABELS]),
        formality=formality,
        sentence_style=sentence_style,
        uses_contractions=uses_contractions,
        prefers_first_person=prefers_first_person,
        opening_style=opening_style,
        strengths=strengths,
        avoid=avoid,
        confidence=confidence,
    )


async def _refine_with_llm(
    settings: Settings,
    samples: list[str],
    baseline: VoiceProfile,
) -> VoiceProfile:
    client = _build_client(settings)
    system = (
        "You analyze writing style for resume and cover-letter coaching. "
        "Describe only observable writing habits, not personality traits. "
        "Choose tone labels only from: direct, practical, grounded, reflective, technical, collaborative, concise. "
        "Choose formality only from: conversational, semi-formal, formal. "
        "Choose sentence_style only from: short, short_to_medium, medium, long. "
        "Choose opening_style only from: problem_first, context_first, action_first, first_person_direct. "
        "Return ONLY valid JSON with this shape: "
        "{\"tone_labels\": [\"...\"], \"formality\": \"...\", \"sentence_style\": \"...\", "
        "\"uses_contractions\": true, \"prefers_first_person\": true, \"opening_style\": \"...\", "
        "\"strengths\": [\"...\"], \"avoid\": [\"...\"], \"confidence\": \"low|medium|high\"}."
    )
    user = (
        f"Observed baseline:\n{baseline.model_dump_json(indent=2)}\n\n"
        "Writing samples:\n"
        + "\n".join(f"- {sample}" for sample in samples[:8])
    )
    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.1,
        max_tokens=300,
    )
    raw = response.choices[0].message.content or "{}"
    parsed = json.loads(raw)
    return VoiceProfile(
        tone_labels=_sanitize_tone_labels(parsed.get("tone_labels")),
        formality=_sanitize_enum(parsed.get("formality"), _ALLOWED_FORMALITY, baseline.formality),
        sentence_style=_sanitize_enum(
            parsed.get("sentence_style"),
            _ALLOWED_SENTENCE_STYLES,
            baseline.sentence_style,
        ),
        uses_contractions=_sanitize_optional_bool(parsed.get("uses_contractions"), baseline.uses_contractions),
        prefers_first_person=_sanitize_optional_bool(
            parsed.get("prefers_first_person"),
            baseline.prefers_first_person,
        ),
        opening_style=_sanitize_enum(
            parsed.get("opening_style"),
            _ALLOWED_OPENING_STYLES,
            baseline.opening_style,
        ),
        strengths=_sanitize_string_list(parsed.get("strengths"), fallback=baseline.strengths),
        avoid=_sanitize_string_list(parsed.get("avoid"), fallback=baseline.avoid),
        confidence=_sanitize_enum(parsed.get("confidence"), {"low", "medium", "high"}, baseline.confidence),
    )


def _merge_voice_profiles(baseline: VoiceProfile, refined: VoiceProfile) -> VoiceProfile:
    return VoiceProfile(
        tone_labels=_unique_preserving_order([*refined.tone_labels, *baseline.tone_labels]),
        formality=refined.formality or baseline.formality,
        sentence_style=refined.sentence_style or baseline.sentence_style,
        uses_contractions=refined.uses_contractions
        if refined.uses_contractions is not None
        else baseline.uses_contractions,
        prefers_first_person=refined.prefers_first_person
        if refined.prefers_first_person is not None
        else baseline.prefers_first_person,
        opening_style=refined.opening_style or baseline.opening_style,
        strengths=_unique_preserving_order([*refined.strengths, *baseline.strengths]),
        avoid=_unique_preserving_order([*refined.avoid, *baseline.avoid]),
        confidence=refined.confidence or baseline.confidence,
    )


def _clean_samples(samples: list[str]) -> list[str]:
    return _unique_preserving_order([sample.strip() for sample in samples if sample.strip()])


def _infer_opening_style(samples: list[str]) -> str:
    first = samples[0].strip().lower() if samples else ""
    if first.startswith("i "):
        return "first_person_direct"
    if first.startswith(("when ", "at ", "in ", "the ", "because ")):
        return "context_first"
    if first.startswith(("built ", "designed ", "delivered ", "created ", "led ")):
        return "action_first"
    if any(word in first for word in ("problem", "challenge", "needed", "pain point")):
        return "problem_first"
    return "context_first"


def _infer_strengths(tone_labels: list[str]) -> list[str]:
    strengths: list[str] = []
    if "direct" in tone_labels:
        strengths.append("clear ownership language")
    if "practical" in tone_labels:
        strengths.append("grounded, delivery-focused phrasing")
    if "technical" in tone_labels:
        strengths.append("comfort with concrete technical detail")
    if "reflective" in tone_labels:
        strengths.append("good at explaining how you approach work")
    if not strengths:
        strengths.append("plain, low-hype wording")
    return strengths[:3]


def _sanitize_tone_labels(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return _unique_preserving_order(
        [label for label in (str(item).strip() for item in value) if label in _ALLOWED_TONE_LABELS]
    )


def _sanitize_string_list(value: object, *, fallback: list[str]) -> list[str]:
    if not isinstance(value, list):
        return fallback
    cleaned = _unique_preserving_order([str(item).strip() for item in value if str(item).strip()])
    return cleaned or fallback


def _sanitize_enum(value: object, allowed: set[str], fallback: str) -> str:
    cleaned = str(value).strip()
    return cleaned if cleaned in allowed else fallback


def _sanitize_optional_bool(value: object, fallback: bool | None) -> bool | None:
    if isinstance(value, bool):
        return value
    return fallback


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
