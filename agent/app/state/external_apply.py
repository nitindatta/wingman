"""State contracts for Envoy's external apply harness.

These models define the boundary between the browser observer, the LLM planner,
the deterministic safety policy, and the LangGraph apply workflow. The harness
uses one proposed action per loop so every browser change can be audited.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


PageType = Literal[
    "unknown",
    "login",
    "form",
    "resume_upload",
    "screening_questions",
    "review",
    "final_submit",
    "confirmation",
    "captcha",
]

ActionType = Literal[
    "fill_text",
    "select_option",
    "set_checkbox",
    "set_radio",
    "upload_file",
    "click",
    "ask_user",
    "stop_ready_to_submit",
    "stop_failed",
]

RiskLevel = Literal["low", "medium", "high"]
ValueSource = Literal["profile", "memory", "user", "inferred", "page", "none"]
ControlKind = Literal[
    "native_text",
    "native_select",
    "native_checkbox",
    "native_radio_group",
    "aria_checkbox",
    "aria_radio_group",
    "aria_combobox",
    "button_listbox",
    "prompt_select",
    "file_upload",
    "unknown",
]
LabelQuality = Literal["missing", "weak", "good"]
DocumentKind = Literal["resume", "cover_letter", "additional_document", "unknown"]
FieldSensitivity = Literal["none", "personal_sensitive", "judgement", "credential"]
FieldAnswerability = Literal[
    "already_answered",
    "profile",
    "memory",
    "inferable",
    "user_required",
    "unsafe_unknown",
    "optional_skip",
]
HarnessStatus = Literal[
    "running",
    "paused_for_user",
    "paused_for_approval",
    "email_verification_required",
    "ready_to_submit",
    "completed",
    "failed",
]
PolicyDecisionType = Literal["allowed", "paused", "rejected"]
PolicyPauseReason = Literal["needs_user_input", "needs_approval", "low_confidence", "sensitive", "final_submit"]


class ObservedField(BaseModel):
    element_id: str
    label: str
    field_type: str
    control_kind: ControlKind | None = None
    required: bool = False
    current_value: str | None = None
    options: list[str] = Field(default_factory=list)
    nearby_text: str = ""
    disabled: bool = False
    visible: bool = True
    invalid: bool = False
    validation_message: str | None = None
    label_quality: LabelQuality | None = None
    profile_fact: str | None = None
    document_kind: DocumentKind | None = None
    sensitivity: FieldSensitivity | None = None
    answerability: FieldAnswerability | None = None
    insight_reason: str | None = None


class ObservedAction(BaseModel):
    element_id: str
    label: str
    kind: Literal["button", "link", "submit", "unknown"] = "unknown"
    href: str | None = None
    disabled: bool = False
    nearby_text: str = ""


class PageObservation(BaseModel):
    url: str
    title: str = ""
    page_type: PageType = "unknown"
    visible_text: str = ""
    fields: list[ObservedField] = Field(default_factory=list)
    buttons: list[ObservedAction] = Field(default_factory=list)
    links: list[ObservedAction] = Field(default_factory=list)
    uploads: list[ObservedField] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    screenshot_ref: str | None = None


class ProposedAction(BaseModel):
    action_type: ActionType
    element_id: str | None = None
    value: str | None = None
    question: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    risk: RiskLevel
    reason: str
    source: ValueSource = "none"


class PolicyDecision(BaseModel):
    decision: PolicyDecisionType
    reason: str
    pause_reason: PolicyPauseReason | None = None
    risk_flags: list[str] = Field(default_factory=list)


class ActionArtifact(BaseModel):
    type: str
    path: str


class ActionResult(BaseModel):
    ok: bool
    action_type: ActionType
    element_id: str | None = None
    message: str = ""
    value_after: str | None = None
    navigated: bool = False
    new_url: str | None = None
    errors: list[str] = Field(default_factory=list)
    artifacts: list[ActionArtifact] = Field(default_factory=list)
    diagnostics: dict[str, object] | None = None


class ActionTrace(BaseModel):
    observation: PageObservation
    proposed_action: ProposedAction
    policy_decision: PolicyDecisionType
    result: ActionResult | None = None


class UserQuestion(BaseModel):
    question: str
    context: str = ""
    suggested_answers: list[str] = Field(default_factory=list)
    target_element_id: str | None = None
    question_key: str | None = None


class ExternalApplyMemoryContext(BaseModel):
    portal_host: str = ""
    portal_identity: str = ""
    account_mode: str | None = None
    account_status: str | None = None
    account_email: str | None = None
    credential_available: bool = False
    credential_status: str | None = None
    login_attempted: bool = False
    saved_login_rejected: bool = False
    create_account_available: bool = False
    recommendations: list[str] = Field(default_factory=list)
    rejected_attempts: list[dict[str, str]] = Field(default_factory=list)
    recent_failures: list[dict[str, object]] = Field(default_factory=list)


PlanningPhase = Literal[
    "unknown",
    "login",
    "account_recovery",
    "profile_form",
    "document_upload",
    "screening",
    "review",
    "final_submit",
    "captcha",
]


class PlanningFrame(BaseModel):
    phase: PlanningPhase = "unknown"
    objective: str = "Plan the safest next application action from the current observation."
    strategies: list[str] = Field(default_factory=list)
    hints: list[str] = Field(default_factory=list)
    recommended_actions: list[dict[str, object]] = Field(default_factory=list)
    blocked_actions: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)


class ExternalApplyState(BaseModel):
    application_id: str
    current_url: str = ""
    page_type: PageType = "unknown"

    observation: PageObservation | None = None
    proposed_action: ProposedAction | None = None
    last_action_result: ActionResult | None = None
    memory_context: ExternalApplyMemoryContext | None = None
    planning_frame: PlanningFrame | None = None

    completed_actions: list[ActionTrace] = Field(default_factory=list)
    pending_user_question: UserQuestion | None = None
    pending_user_questions: list[UserQuestion] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)

    submit_ready: bool = False
    status: HarnessStatus = "running"
    error: str | None = None
