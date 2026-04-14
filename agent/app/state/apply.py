"""Domain models for the apply phase."""

from __future__ import annotations

from pydantic import BaseModel, Field


class FieldInfo(BaseModel):
    id: str
    label: str
    field_type: str
    required: bool
    current_value: str | None = None
    options: list[str] | None = None
    max_length: int | None = None


class StepInfo(BaseModel):
    page_url: str
    page_type: str  # form | confirmation | external_redirect | unknown
    step_index: int | None = None
    total_steps_estimate: int | None = None
    is_external_portal: bool = False
    portal_type: str | None = None
    fields: list[FieldInfo] = Field(default_factory=list)
    visible_actions: list[str] = Field(default_factory=list)


class ApplyState(BaseModel):
    """LangGraph workflow state for the apply phase."""

    # Identity
    application_id: str
    workflow_run_id: str
    session_key: str = ""

    # Current step
    current_step: StepInfo | None = None
    proposed_values: dict[str, str] = Field(default_factory=dict)
    low_confidence_ids: list[str] = Field(default_factory=list)  # fields needing human review
    action_label: str = "Continue"  # action the user wants to click on resume

    # Accumulation
    step_history: list[dict] = Field(default_factory=list)

    # Pre-submit pause
    submit_action_label: str = "Continue"  # label of the final Submit button on SEEK

    # Terminal state
    status: str = "running"  # running | paused | awaiting_submit | completed | failed | aborted
    error: str | None = None
    pause_reason: str | None = None  # auth_required | drift | etc.


class ApplyRequest(BaseModel):
    application_id: str


class ApplyStepResponse(BaseModel):
    """Returned when workflow pauses at an interrupt."""

    workflow_run_id: str
    status: str  # paused | awaiting_submit | completed | failed | aborted
    step: StepInfo | None = None
    proposed_values: dict[str, str] = Field(default_factory=dict)
    low_confidence_ids: list[str] = Field(default_factory=list)
    submit_action_label: str = "Continue"
    step_history: list[dict] = Field(default_factory=list)
    error: str | None = None
    pause_reason: str | None = None


class ApplyResumeRequest(BaseModel):
    approved_values: dict[str, str]
    action_label: str = "Continue"
    action: str = "continue"  # "continue" | "abort"
