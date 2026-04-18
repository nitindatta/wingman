"""State and API models for the profile interview workflow."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.state.canonical_profile import CanonicalEvidenceItem, CanonicalProfile


class ProfileInterviewPrompt(BaseModel):
    question_id: str = ""
    question: str = ""
    suggested_answer: str = ""
    source_basis: list[str] = Field(default_factory=list)
    improvement_hint: str = ""


class ProfileInterviewAnswerAssessment(BaseModel):
    score: float = 0.0
    dimension_scores: dict[str, float] = Field(default_factory=dict)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    next_focus: str = ""
    confidence: str = "draft"


class ProfileInterviewState(BaseModel):
    session_id: str
    source_profile_path: str
    target_profile_path: str
    canonical_profile: CanonicalProfile

    action: str = "start"  # start | select | answer | approve | defer | complete
    status: str = "idle"  # waiting_for_user | reviewing | completed | error

    current_item_id: str = ""
    selected_item_id: str = ""
    draft_item: CanonicalEvidenceItem | None = None
    deferred_item_ids: list[str] = Field(default_factory=list)
    open_gaps: list[str] = Field(default_factory=list)
    current_gap: str = ""
    current_question_id: str = ""
    current_question: str = ""
    current_prompt: ProfileInterviewPrompt = Field(default_factory=ProfileInterviewPrompt)

    user_answer: str = ""
    last_interpretation: dict[str, object] = Field(default_factory=dict)
    last_answer_assessment: ProfileInterviewAnswerAssessment = Field(
        default_factory=ProfileInterviewAnswerAssessment
    )
    item_quality_scores: dict[str, float] = Field(default_factory=dict)
    item_quality_counts: dict[str, int] = Field(default_factory=dict)
    overall_answer_quality_score: float | None = None
    overall_profile_score: float | None = None
    asked_question_ids: list[str] = Field(default_factory=list)
    turn_count: int = 0
    completeness_score: float = 0.0

    error: str | None = None


class StartProfileInterviewRequest(BaseModel):
    item_id: str | None = None


class SelectProfileInterviewRequest(BaseModel):
    item_id: str


class AnswerProfileInterviewRequest(BaseModel):
    answer: str


class ApproveProfileInterviewRequest(BaseModel):
    pass


class DeferProfileInterviewRequest(BaseModel):
    pass


class CompleteProfileInterviewRequest(BaseModel):
    pass


class ProfileInterviewSessionResponse(BaseModel):
    session_id: str
    status: str
    source_profile_path: str
    target_profile_path: str
    current_item_id: str = ""
    draft_item: CanonicalEvidenceItem | None = None
    open_gaps: list[str] = Field(default_factory=list)
    current_gap: str = ""
    current_question_id: str = ""
    current_question: str = ""
    current_prompt: ProfileInterviewPrompt = Field(default_factory=ProfileInterviewPrompt)
    last_answer_assessment: ProfileInterviewAnswerAssessment = Field(
        default_factory=ProfileInterviewAnswerAssessment
    )
    item_quality_scores: dict[str, float] = Field(default_factory=dict)
    completeness_score: float = 0.0
    overall_answer_quality_score: float | None = None
    overall_profile_score: float | None = None
    approved_items: int = 0
    total_items: int = 0
    error: str | None = None
