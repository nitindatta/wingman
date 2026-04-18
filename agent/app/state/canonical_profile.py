"""Canonical application profile models.

These models represent the normalized, STAR-friendly profile we want the
agent to write from. Raw resumes and ad-hoc profile JSON are source material;
the cover-letter workflow should eventually use this canonical shape instead.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class VoiceProfile(BaseModel):
    tone_labels: list[str] = Field(default_factory=list)
    formality: str = ""
    sentence_style: str = ""
    uses_contractions: bool | None = None
    prefers_first_person: bool | None = None
    opening_style: str = ""
    strengths: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    confidence: str = "draft"


class CanonicalEvidenceItem(BaseModel):
    id: str
    source: str
    role_title: str | None = None
    skills: list[str] = Field(default_factory=list)
    domain: list[str] = Field(default_factory=list)
    situation: str = ""
    task: str = ""
    action: str = ""
    outcome: str = ""
    metrics: list[str] = Field(default_factory=list)
    proof_points: list[str] = Field(default_factory=list)
    tone_sample: str | None = None
    confidence: str = "draft"


class CanonicalProfile(BaseModel):
    name: str = ""
    headline: str = ""
    summary: str = ""
    location: str | None = None
    work_rights: str | None = None
    salary_expectation: str | None = None
    core_strengths: list[str] = Field(default_factory=list)
    voice_samples: list[str] = Field(default_factory=list)
    voice_profile: VoiceProfile = Field(default_factory=VoiceProfile)
    evidence_items: list[CanonicalEvidenceItem] = Field(default_factory=list)


class ProfileEnrichmentQuestion(BaseModel):
    id: str
    evidence_item_id: str | None = None
    target_field: str
    prompt: str
    help_text: str = ""
    priority: str = "medium"
    input_type: str = "text"
    current_value: str | None = None


class ProfileAnswer(BaseModel):
    question_id: str | None = None
    target_field: str
    value: str = ""


class ProfileTargetResponse(BaseModel):
    profile_exists: bool
    source_profile_path: str
    target_profile_path: str
    target_profile_exists: bool
    target_profile: CanonicalProfile | None = None
    questions: list[ProfileEnrichmentQuestion] = Field(default_factory=list)
