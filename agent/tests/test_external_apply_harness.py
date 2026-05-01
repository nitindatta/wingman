from typing import Any

from app.services.external_apply_harness import (
    apply_external_user_answer,
    apply_external_user_answers,
    plan_external_apply_step,
    realign_external_state_to_observation,
    run_external_apply_step,
)
from app.services.external_apply_policy import validate_external_apply_action
from app.state.external_apply import (
    ActionResult,
    ActionTrace,
    ExternalApplyState,
    ObservedAction,
    ObservedField,
    PageObservation,
    PolicyDecision,
    ProposedAction,
    UserQuestion,
)


class DummySettings:
    pass


class DummyToolClient:
    pass


class FakeQuestionCache:
    def __init__(self, found: str | None = None) -> None:
        self.found = found
        self.saved: list[tuple[str, str, str | None, str]] = []

    async def find(self, _question_raw: str) -> str | None:
        return self.found

    async def save(
        self,
        question_raw: str,
        answer: str,
        field_type: str | None = None,
        source: str = "human",
    ) -> str:
        self.saved.append((question_raw, answer, field_type, source))
        return "cache-id"


def test_validate_external_apply_action_rejects_skip_navigation_clicks() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        page_type="unknown",
        links=[
            ObservedAction(
                element_id="link_4",
                label="Skip to main content",
                kind="link",
                href="",
                nearby_text="Skip to main content",
            )
        ],
    )
    proposed = ProposedAction(
        action_type="click",
        element_id="link_4",
        confidence=0.96,
        risk="low",
        reason="Proceed with the next visible action.",
        source="page",
    )

    decision = validate_external_apply_action(
        observation=observation,
        proposed_action=proposed,
        profile_facts={},
    )

    assert decision.decision == "rejected"
    assert decision.risk_flags == ["utility_navigation"]


async def test_plan_external_apply_step_returns_running_for_browser_action() -> None:
    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return PageObservation(
            url="https://ats.example/apply",
            page_type="form",
            fields=[ObservedField(element_id="field_1", label="Full name", field_type="text")],
        )

    async def planner(_settings: Any, **_kwargs: Any) -> ProposedAction:
        return ProposedAction(
            action_type="fill_text",
            element_id="field_1",
            value="Nitin Datta",
            confidence=0.95,
            risk="low",
            reason="Full name comes from profile.",
            source="profile",
        )

    state = await plan_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={"name": "Nitin Datta"},
        observe_fn=observe,
        planner_fn=planner,
    )

    assert state.status == "running"
    assert state.current_url == "https://ats.example/apply"
    assert state.page_type == "form"
    assert state.proposed_action is not None
    assert state.proposed_action.action_type == "fill_text"


async def test_plan_external_apply_step_pauses_for_user_question() -> None:
    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return PageObservation(
            url="https://ats.example/apply",
            page_type="form",
            fields=[ObservedField(element_id="field_salary", label="Expected salary", field_type="text")],
        )

    async def planner(_settings: Any, **_kwargs: Any) -> ProposedAction:
        return ProposedAction(
            action_type="ask_user",
            element_id="field_salary",
            question="What salary should I enter?",
            confidence=0.9,
            risk="medium",
            reason="Salary needs user confirmation.",
            source="page",
        )

    state = await plan_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={},
        observe_fn=observe,
        planner_fn=planner,
    )

    assert state.status == "paused_for_user"
    assert state.pending_user_question is not None
    assert state.pending_user_question.question == "What salary should I enter?"
    assert state.pending_user_question.target_element_id == "field_salary"


async def test_plan_external_apply_step_splits_compound_user_question_into_field_questions() -> None:
    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return PageObservation(
            url="https://secure.dc2.pageuppeople.com/apply",
            title="Diversity",
            page_type="form",
            fields=[
                ObservedField(
                    element_id="field_birth_country",
                    label="Please enter your country of birth.*",
                    field_type="text",
                    required=True,
                ),
                ObservedField(
                    element_id="field_diverse",
                    label="Are you from a linguistically and/or culturally diverse background?*",
                    field_type="select",
                    current_value="Select",
                    required=True,
                    options=["Select", "Yes", "No", "Prefer not to say"],
                ),
                ObservedField(
                    element_id="field_language_1",
                    label="Language 1:",
                    field_type="select",
                    current_value="English",
                    options=["Select", "English", "Hindi"],
                ),
                ObservedField(
                    element_id="field_language_1_speaking",
                    label="Language 1 Speaking proficiency",
                    field_type="select",
                    current_value="Select",
                    options=["Select", "None", "Basic", "Intermediate", "Proficient", "Fluent"],
                ),
                ObservedField(
                    element_id="field_language_1_reading",
                    label="Language 1 Reading proficiency",
                    field_type="select",
                    current_value="Select",
                    options=["Select", "None", "Basic", "Intermediate", "Proficient", "Fluent"],
                ),
                ObservedField(
                    element_id="field_language_1_writing",
                    label="Language 1 Writing proficiency",
                    field_type="select",
                    current_value="Select",
                    options=["Select", "None", "Basic", "Intermediate", "Proficient", "Fluent"],
                ),
                ObservedField(
                    element_id="field_language_2",
                    label="Language 2:",
                    field_type="select",
                    current_value="Hindi",
                    options=["Select", "English", "Hindi"],
                ),
                ObservedField(
                    element_id="field_language_2_speaking",
                    label="Language 2 Speaking proficiency",
                    field_type="select",
                    current_value="Select",
                    options=["Select", "None", "Basic", "Intermediate", "Proficient", "Fluent"],
                ),
                ObservedField(
                    element_id="field_language_2_reading",
                    label="Language 2 Reading proficiency",
                    field_type="select",
                    current_value="Select",
                    options=["Select", "None", "Basic", "Intermediate", "Proficient", "Fluent"],
                ),
                ObservedField(
                    element_id="field_language_2_writing",
                    label="Language 2 Writing proficiency",
                    field_type="select",
                    current_value="Select",
                    options=["Select", "None", "Basic", "Intermediate", "Proficient", "Fluent"],
                ),
            ],
        )

    async def planner(_settings: Any, **_kwargs: Any) -> ProposedAction:
        return ProposedAction(
            action_type="ask_user",
            element_id="field_birth_country",
            question=(
                "Please provide the remaining answers needed for this Diversity page: "
                "1) What is your country of birth? "
                "2) Are you from a linguistically and/or culturally diverse background? Options: Yes, No, or Prefer not to say. "
                "3) For Language 1 English, what are your speaking, reading, and writing proficiencies? "
                "4) For Language 2 Hindi, what are your speaking, reading, and writing proficiencies?"
            ),
            confidence=0.99,
            risk="medium",
            reason="The remaining unresolved fields require user judgement.",
            source="none",
        )

    state = await plan_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={},
        observe_fn=observe,
        planner_fn=planner,
    )

    assert state.status == "paused_for_user"
    assert [question.target_element_id for question in state.pending_user_questions] == [
        "field_birth_country",
        "field_diverse",
        "field_language_1_speaking",
        "field_language_1_reading",
        "field_language_1_writing",
        "field_language_2_speaking",
        "field_language_2_reading",
        "field_language_2_writing",
    ]
    assert state.pending_user_question is not None
    assert state.pending_user_question.target_element_id == "field_birth_country"


async def test_plan_external_apply_step_marks_ready_to_submit() -> None:
    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return PageObservation(
            url="https://ats.example/review",
            page_type="review",
            buttons=[ObservedAction(element_id="button_submit", label="Submit application", kind="submit")],
        )

    async def planner(_settings: Any, **_kwargs: Any) -> ProposedAction:
        return ProposedAction(
            action_type="stop_ready_to_submit",
            element_id="button_submit",
            confidence=0.94,
            risk="high",
            reason="Final submission needs human approval.",
            source="page",
        )

    state = await plan_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={},
        observe_fn=observe,
        planner_fn=planner,
    )

    assert state.status == "ready_to_submit"
    assert state.submit_ready is True


async def test_run_external_apply_step_executes_when_policy_allows() -> None:
    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return PageObservation(
            url="https://ats.example/apply",
            page_type="form",
            fields=[ObservedField(element_id="field_1", label="Full name", field_type="text")],
        )

    async def planner(_settings: Any, **_kwargs: Any) -> ProposedAction:
        return ProposedAction(
            action_type="fill_text",
            element_id="field_1",
            value="Nitin Datta",
            confidence=0.95,
            risk="low",
            reason="Full name comes from profile.",
            source="profile",
        )

    def policy(**_kwargs: Any) -> PolicyDecision:
        return PolicyDecision(decision="allowed", reason="safe")

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        return ActionResult(
            ok=True,
            action_type=action.action_type,
            element_id=action.element_id,
            message="action executed",
            value_after=action.value,
            new_url="https://ats.example/apply",
        )

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={"name": "Nitin Datta"},
        observe_fn=observe,
        planner_fn=planner,
        policy_fn=policy,
        execute_fn=execute,
    )

    assert state.status == "running"
    assert state.last_action_result is not None
    assert state.last_action_result.ok is True
    assert len(state.completed_actions) == 1
    assert state.completed_actions[0].policy_decision == "allowed"


async def test_run_external_apply_step_uploads_generated_cover_letter_before_planner(tmp_path) -> None:
    cover_letter_path = tmp_path / "cover-letter.txt"
    cover_letter_path.write_text("Dear Hiring Team,\n\nI am excited to apply.\n", encoding="utf-8")
    executed: list[ProposedAction] = []

    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return PageObservation(
            url="https://ats.example/apply",
            page_type="resume_upload",
            fields=[ObservedField(element_id="field_cover", label="Cover letter", field_type="file", required=False)],
        )

    async def planner(_settings: Any, **_kwargs: Any) -> ProposedAction:
        raise AssertionError("cover-letter upload should be chosen before the LLM planner")

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        executed.append(action)
        return ActionResult(ok=True, action_type=action.action_type, element_id=action.element_id, value_after=action.value)

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={"cover_letter_path": str(cover_letter_path)},
        observe_fn=observe,
        planner_fn=planner,
        batch_planner_fn=None,
        execute_fn=execute,
    )

    assert state.status == "running"
    assert executed[0].action_type == "upload_file"
    assert executed[0].element_id == "field_cover"
    assert executed[0].value == str(cover_letter_path)


async def test_run_external_apply_step_pastes_generated_cover_letter_before_planner() -> None:
    cover_letter = "Dear Hiring Team,\n\nI am excited to apply.\n\nKind regards,\nNitin"
    executed: list[ProposedAction] = []

    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return PageObservation(
            url="https://ats.example/apply",
            page_type="form",
            fields=[ObservedField(element_id="field_cover", label="Cover letter", field_type="textarea", required=True)],
        )

    async def planner(_settings: Any, **_kwargs: Any) -> ProposedAction:
        raise AssertionError("cover-letter textarea should be chosen before the LLM planner")

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        executed.append(action)
        return ActionResult(ok=True, action_type=action.action_type, element_id=action.element_id, value_after=action.value)

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={"cover_letter": cover_letter},
        observe_fn=observe,
        planner_fn=planner,
        batch_planner_fn=None,
        execute_fn=execute,
    )

    assert state.status == "running"
    assert executed[0].action_type == "fill_text"
    assert executed[0].element_id == "field_cover"
    assert executed[0].value == cover_letter


async def test_run_external_apply_step_continues_after_required_resume_and_cover_letter_uploads() -> None:
    resume_path = "C:/workspace/profile/resume.docx"
    cover_letter_path = "C:/workspace/automation/cover_letters/app-1_cover_letter.txt"
    resume_observation = PageObservation(
        url="https://ats.example/apply/resume",
        page_type="resume_upload",
        fields=[
            ObservedField(element_id="field_resume", label="Please attach your resume*", field_type="file", required=True),
            ObservedField(element_id="field_cover", label="Please attach your cover letter", field_type="file", required=False),
            ObservedField(
                element_id="field_other",
                label="Please attach any other relevant documentation (optional)",
                field_type="file",
                required=False,
            ),
        ],
    )
    current_observation = PageObservation(
        url="https://ats.example/apply/resume",
        page_type="resume_upload",
        visible_text="Your current resume must be uploaded in order to submit this application.",
        fields=[
            ObservedField(
                element_id="field_other",
                label="Please attach any other relevant documentation (optional)",
                field_type="file",
                required=False,
            )
        ],
        buttons=[ObservedAction(element_id="button_continue", label="Continue", kind="submit")],
        errors=["Your current resume must be uploaded in order to submit this application."],
    )
    recent_actions = [
        ActionTrace(
            observation=resume_observation,
            proposed_action=ProposedAction(
                action_type="upload_file",
                element_id="field_cover",
                value=cover_letter_path,
                confidence=1.0,
                risk="low",
                reason="Upload generated cover letter.",
                source="profile",
            ),
            policy_decision="allowed",
            result=ActionResult(ok=True, action_type="upload_file", element_id="field_cover", value_after=cover_letter_path),
        ),
        ActionTrace(
            observation=resume_observation,
            proposed_action=ProposedAction(
                action_type="upload_file",
                element_id="field_resume",
                value=resume_path,
                confidence=1.0,
                risk="low",
                reason="Upload configured resume.",
                source="profile",
            ),
            policy_decision="allowed",
            result=ActionResult(ok=True, action_type="upload_file", element_id="field_resume", value_after=resume_path),
        ),
    ]
    executed: list[ProposedAction] = []

    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return current_observation

    async def planner(_settings: Any, **_kwargs: Any) -> ProposedAction:
        raise AssertionError("completed required uploads should continue before the LLM planner can stop_failed")

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        executed.append(action)
        return ActionResult(ok=True, action_type=action.action_type, element_id=action.element_id, navigated=True)

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={"resume_path": resume_path, "cover_letter_path": cover_letter_path},
        recent_actions=recent_actions,
        observe_fn=observe,
        planner_fn=planner,
        batch_planner_fn=None,
        execute_fn=execute,
    )

    assert state.status == "running"
    assert executed[0].action_type == "click"
    assert executed[0].element_id == "button_continue"


async def test_run_external_apply_step_executes_safe_batch_before_pausing_once() -> None:
    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return PageObservation(
            url="https://ats.example/apply",
            page_type="form",
            fields=[
                ObservedField(element_id="field_1", label="First name", field_type="text"),
                ObservedField(element_id="field_2", label="Last name", field_type="text"),
                ObservedField(element_id="field_3", label="Home address", field_type="text", required=True),
            ],
        )

    async def batch_planner(_settings: Any, **_kwargs: Any) -> list[ProposedAction]:
        return [
            ProposedAction(
                action_type="fill_text",
                element_id="field_1",
                value="Nitin",
                confidence=0.96,
                risk="low",
                reason="First name comes from profile.",
                source="profile",
            ),
            ProposedAction(
                action_type="fill_text",
                element_id="field_2",
                value="Datta",
                confidence=0.96,
                risk="low",
                reason="Last name comes from profile.",
                source="profile",
            ),
            ProposedAction(
                action_type="ask_user",
                element_id="field_3",
                question="What home address should I enter?",
                confidence=0.95,
                risk="medium",
                reason="Address is not available in approved profile facts.",
                source="none",
            ),
        ]

    def policy(*, proposed_action: ProposedAction, **_kwargs: Any) -> PolicyDecision:
        if proposed_action.action_type == "ask_user":
            return PolicyDecision(
                decision="paused",
                reason=proposed_action.reason,
                pause_reason="needs_user_input",
                risk_flags=["user_input_required"],
            )
        return PolicyDecision(decision="allowed", reason="safe")

    executed: list[str | None] = []

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        executed.append(action.element_id)
        return ActionResult(
            ok=True,
            action_type=action.action_type,
            element_id=action.element_id,
            message="action executed",
            value_after=action.value,
            new_url="https://ats.example/apply",
        )

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={"first_name": "Nitin", "last_name": "Datta"},
        observe_fn=observe,
        batch_planner_fn=batch_planner,
        policy_fn=policy,
        execute_fn=execute,
    )

    assert executed == ["field_1", "field_2"]
    assert state.status == "paused_for_user"
    assert state.pending_user_question is not None
    assert state.pending_user_question.target_element_id == "field_3"
    assert len(state.completed_actions) == 3
    assert state.completed_actions[-1].policy_decision == "paused"


async def test_run_external_apply_step_reobserves_before_terminal_action_after_fill() -> None:
    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return PageObservation(
            url="https://ats.example/login",
            page_type="login",
            fields=[ObservedField(element_id="field_email", label="Email", field_type="text")],
        )

    async def batch_planner(_settings: Any, **_kwargs: Any) -> list[ProposedAction]:
        return [
            ProposedAction(
                action_type="fill_text",
                element_id="field_email",
                value="candidate@example.com",
                confidence=0.96,
                risk="low",
                reason="Email comes from profile.",
                source="profile",
            ),
            ProposedAction(
                action_type="stop_ready_to_submit",
                confidence=0.92,
                risk="high",
                reason="Incorrectly thought the page was ready for final submit.",
                source="page",
            ),
        ]

    executed: list[str | None] = []

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        executed.append(action.element_id)
        return ActionResult(
            ok=True,
            action_type=action.action_type,
            element_id=action.element_id,
            value_after=action.value,
            new_url="https://ats.example/login",
        )

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={"email": "candidate@example.com"},
        observe_fn=observe,
        batch_planner_fn=batch_planner,
        execute_fn=execute,
    )

    assert executed == ["field_email"]
    assert state.status == "running"
    assert state.submit_ready is False
    assert state.completed_actions[-1].proposed_action.action_type == "fill_text"


async def test_run_external_apply_step_collects_multiple_user_questions_from_batch_plan() -> None:
    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return PageObservation(
            url="https://ats.example/apply",
            page_type="form",
            fields=[
                ObservedField(element_id="field_name", label="First name", field_type="text"),
                ObservedField(element_id="field_email", label="Email", field_type="text", required=True),
                ObservedField(element_id="field_password", label="Password", field_type="text", required=True),
            ],
        )

    async def batch_planner(_settings: Any, **_kwargs: Any) -> list[ProposedAction]:
        return [
            ProposedAction(
                action_type="fill_text",
                element_id="field_name",
                value="Nitin",
                confidence=0.96,
                risk="low",
                reason="First name comes from profile.",
                source="profile",
            ),
            ProposedAction(
                action_type="ask_user",
                element_id="field_email",
                question="What exact email address should be used?",
                confidence=0.85,
                risk="medium",
                reason="No exact email is available in approved facts.",
                source="page",
            ),
            ProposedAction(
                action_type="ask_user",
                element_id="field_password",
                question="What password should be used?",
                confidence=0.85,
                risk="medium",
                reason="Passwords must come from the user.",
                source="page",
            ),
        ]

    def policy(*, proposed_action: ProposedAction, **_kwargs: Any) -> PolicyDecision:
        if proposed_action.action_type == "ask_user":
            return PolicyDecision(
                decision="paused",
                reason=proposed_action.reason,
                pause_reason="needs_user_input",
                risk_flags=["user_input_required"],
            )
        return PolicyDecision(decision="allowed", reason="safe")

    executed: list[str | None] = []

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        executed.append(action.element_id)
        return ActionResult(
            ok=True,
            action_type=action.action_type,
            element_id=action.element_id,
            message="action executed",
            value_after=action.value,
            new_url="https://ats.example/apply",
        )

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={"first_name": "Nitin"},
        observe_fn=observe,
        batch_planner_fn=batch_planner,
        policy_fn=policy,
        execute_fn=execute,
    )

    assert executed == ["field_name"]
    assert state.status == "paused_for_user"
    assert state.pending_user_question is not None
    assert state.pending_user_question.target_element_id == "field_email"
    assert [question.target_element_id for question in state.pending_user_questions] == [
        "field_email",
        "field_password",
    ]
    assert len(state.completed_actions) == 2
    assert state.completed_actions[-1].policy_decision == "paused"


async def test_run_external_apply_step_does_not_click_after_batch_fills() -> None:
    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return PageObservation(
            url="https://ats.example/apply",
            page_type="form",
            fields=[ObservedField(element_id="field_1", label="First name", field_type="text")],
            buttons=[ObservedAction(element_id="button_next", label="Next", kind="button")],
        )

    async def batch_planner(_settings: Any, **_kwargs: Any) -> list[ProposedAction]:
        return [
            ProposedAction(
                action_type="fill_text",
                element_id="field_1",
                value="Nitin",
                confidence=0.96,
                risk="low",
                reason="First name comes from profile.",
                source="profile",
            ),
            ProposedAction(
                action_type="click",
                element_id="button_next",
                confidence=0.96,
                risk="low",
                reason="Continue after profile fields are filled.",
                source="page",
            ),
        ]

    def policy(**_kwargs: Any) -> PolicyDecision:
        return PolicyDecision(decision="allowed", reason="safe")

    executed: list[str | None] = []

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        executed.append(action.element_id)
        return ActionResult(
            ok=True,
            action_type=action.action_type,
            element_id=action.element_id,
            value_after=action.value,
            new_url="https://ats.example/apply",
        )

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={"first_name": "Nitin"},
        observe_fn=observe,
        batch_planner_fn=batch_planner,
        policy_fn=policy,
        execute_fn=execute,
    )

    assert executed == ["field_1"]
    assert state.status == "running"
    assert state.completed_actions[-1].proposed_action.action_type == "fill_text"


async def test_run_external_apply_step_reobserves_and_clicks_after_page_mutation() -> None:
    observations = [
        PageObservation(
            url="https://ats.example/apply",
            page_type="form",
            fields=[ObservedField(element_id="field_1", label="First name", field_type="text")],
            buttons=[ObservedAction(element_id="button_next", label="Next", kind="button")],
        ),
        PageObservation(
            url="https://ats.example/apply",
            page_type="form",
            fields=[ObservedField(element_id="field_1", label="First name", field_type="text", current_value="Nitin")],
            buttons=[ObservedAction(element_id="button_next", label="Next", kind="button")],
        ),
    ]
    observe_calls = 0

    async def observe(_client: Any, _session_key: str) -> PageObservation:
        nonlocal observe_calls
        observe_calls += 1
        return observations[min(observe_calls - 1, len(observations) - 1)]

    async def planner(_settings: Any, **kwargs: Any) -> ProposedAction:
        observation = kwargs["observation"]
        if observation.fields[0].current_value:
            return ProposedAction(
                action_type="click",
                element_id="button_next",
                confidence=0.96,
                risk="low",
                reason="Continue after the page fields are complete.",
                source="page",
            )
        return ProposedAction(
            action_type="fill_text",
            element_id="field_1",
            value="Nitin",
            confidence=0.96,
            risk="low",
            reason="First name comes from profile.",
            source="profile",
        )

    def policy(**_kwargs: Any) -> PolicyDecision:
        return PolicyDecision(decision="allowed", reason="safe")

    executed: list[str | None] = []

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        executed.append(action.element_id)
        return ActionResult(
            ok=True,
            action_type=action.action_type,
            element_id=action.element_id,
            value_after=action.value,
            new_url="https://ats.example/apply/next" if action.action_type == "click" else "https://ats.example/apply",
            navigated=action.action_type == "click",
        )

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={"first_name": "Nitin"},
        observe_fn=observe,
        planner_fn=planner,
        policy_fn=policy,
        execute_fn=execute,
    )

    assert observe_calls == 2
    assert executed == ["field_1", "button_next"]
    assert state.status == "running"
    assert state.last_action_result is not None
    assert state.last_action_result.action_type == "click"
    assert state.current_url == "https://ats.example/apply/next"


async def test_run_external_apply_step_pauses_before_click_when_required_fields_are_incomplete() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        page_type="form",
        errors=["Please answer the required questions before continuing."],
        fields=[
            ObservedField(
                element_id="field_1",
                label="Have you previously worked here?",
                field_type="radio",
                required=True,
                options=["Yes", "No"],
            ),
            ObservedField(
                element_id="field_2",
                label="Phone Device Type",
                field_type="select",
                required=True,
                options=["Mobile", "Landline"],
            ),
        ],
        buttons=[ObservedAction(element_id="button_next", label="Create Account", kind="submit")],
    )

    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return observation

    async def planner(_settings: Any, **_kwargs: Any) -> ProposedAction:
        return ProposedAction(
            action_type="click",
            element_id="button_next",
            confidence=0.95,
            risk="low",
            reason="Continue after filling the account form.",
            source="page",
        )

    def policy(**_kwargs: Any) -> PolicyDecision:
        raise AssertionError("policy should not be called when required fields are still incomplete")

    async def execute(_client: Any, _session_key: str, _action: ProposedAction) -> ActionResult:
        raise AssertionError("execute should not be called when required fields are still incomplete")

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={},
        observe_fn=observe,
        planner_fn=planner,
        policy_fn=policy,
        execute_fn=execute,
    )

    assert state.status == "paused_for_user"
    assert state.pending_user_question is not None
    assert state.pending_user_question.target_element_id == "field_1"
    assert [question.target_element_id for question in state.pending_user_questions] == ["field_1", "field_2"]
    assert "required fields" in state.pending_user_question.context.lower()
    assert "required_fields_incomplete" in state.risk_flags
    assert state.completed_actions[-1].policy_decision == "paused"


async def test_run_external_apply_step_pauses_on_repeated_non_navigating_click_on_same_page() -> None:
    observation = PageObservation(
        url="https://ats.example/login",
        page_type="login",
        errors=["Please enter a valid password."],
        fields=[
            ObservedField(element_id="field_email", label="Email", field_type="text", current_value="nitin@example.com"),
            ObservedField(element_id="field_password", label="Password", field_type="text"),
        ],
        buttons=[ObservedAction(element_id="button_11", label="Sign in", kind="submit")],
    )
    prior_click = ActionTrace(
        observation=observation,
        proposed_action=ProposedAction(
            action_type="click",
            element_id="button_11",
            confidence=0.97,
            risk="low",
            reason="Submit the login form.",
            source="page",
        ),
        policy_decision="allowed",
        result=ActionResult(
            ok=True,
            action_type="click",
            element_id="button_11",
            message="action executed",
            navigated=False,
            new_url="https://ats.example/login",
            errors=["Please enter a valid password."],
        ),
    )

    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return observation

    async def planner(_settings: Any, **_kwargs: Any) -> ProposedAction:
        return ProposedAction(
            action_type="click",
            element_id="button_11",
            confidence=0.96,
            risk="low",
            reason="Submit the login form.",
            source="page",
        )

    def policy(**_kwargs: Any) -> PolicyDecision:
        raise AssertionError("policy should not be called for a stale repeated click")

    async def execute(_client: Any, _session_key: str, _action: ProposedAction) -> ActionResult:
        raise AssertionError("execute should not be called for a stale repeated click")

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={},
        recent_actions=[prior_click],
        observe_fn=observe,
        planner_fn=planner,
        policy_fn=policy,
        execute_fn=execute,
    )

    assert state.status == "paused_for_user"
    assert state.pending_user_question is not None
    assert state.pending_user_question.target_element_id is None
    assert "did not advance" in state.pending_user_question.question.lower()
    assert "Please enter a valid password." in state.pending_user_question.context
    assert "stale_repeated_click" in state.risk_flags
    assert state.completed_actions[-1].policy_decision == "paused"
    assert state.completed_actions[-1].result is None


async def test_run_external_apply_step_waits_for_delayed_transition_before_pausing_on_repeated_click() -> None:
    login_observation = PageObservation(
        url="https://ats.example/login",
        page_type="login",
        errors=["current step 1 of 5", "step 2 of 5"],
        fields=[
            ObservedField(element_id="field_email", label="Email", field_type="text", current_value="nitin@example.com"),
            ObservedField(element_id="field_password", label="Password", field_type="password", current_value="secret"),
        ],
        buttons=[ObservedAction(element_id="button_11", label="Create Account", kind="submit")],
    )
    form_observation = PageObservation(
        url="https://ats.example/apply",
        page_type="form",
        fields=[ObservedField(element_id="field_first_name", label="Given Name*", field_type="text")],
        buttons=[ObservedAction(element_id="button_next", label="Save and Continue", kind="submit")],
    )
    prior_click = ActionTrace(
        observation=login_observation,
        proposed_action=ProposedAction(
            action_type="click",
            element_id="button_11",
            confidence=0.97,
            risk="low",
            reason="Submit the account-creation form.",
            source="page",
        ),
        policy_decision="allowed",
        result=ActionResult(
            ok=True,
            action_type="click",
            element_id="button_11",
            message="action executed",
            navigated=False,
            new_url="https://ats.example/login",
            errors=["current step 1 of 5", "step 2 of 5"],
        ),
    )

    observe_calls = 0

    async def observe(_client: Any, _session_key: str) -> PageObservation:
        nonlocal observe_calls
        observe_calls += 1
        if observe_calls == 1:
            return login_observation
        return form_observation

    async def planner(_settings: Any, **kwargs: Any) -> ProposedAction:
        observation = kwargs["observation"]
        if observation.page_type == "login":
            return ProposedAction(
                action_type="click",
                element_id="button_11",
                confidence=0.96,
                risk="low",
                reason="Submit the account-creation form.",
                source="page",
            )
        return ProposedAction(
            action_type="fill_text",
            element_id="field_first_name",
            value="Nitin",
            confidence=0.96,
            risk="low",
            reason="Given name comes from profile.",
            source="profile",
        )

    def policy(**_kwargs: Any) -> PolicyDecision:
        return PolicyDecision(decision="allowed", reason="safe")

    executed: list[tuple[str, str | None]] = []

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        executed.append((action.action_type, action.element_id))
        return ActionResult(
            ok=True,
            action_type=action.action_type,
            element_id=action.element_id,
            value_after=action.value,
            new_url=form_observation.url,
            navigated=False,
            errors=[],
        )

    async def sleep(_seconds: float) -> None:
        return None

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={"first_name": "Nitin"},
        recent_actions=[prior_click],
        observe_fn=observe,
        planner_fn=planner,
        policy_fn=policy,
        execute_fn=execute,
        sleep_fn=sleep,
    )

    assert observe_calls >= 2
    assert state.status == "running"
    assert state.pending_user_question is None
    assert executed == [("fill_text", "field_first_name")]


async def test_run_external_apply_step_still_pauses_on_stale_click_after_generic_resume_approval() -> None:
    observation = PageObservation(
        url="https://ats.example/login",
        page_type="login",
        errors=["Please enter a valid password."],
        fields=[
            ObservedField(element_id="field_email", label="Email", field_type="text", current_value="nitin@example.com"),
            ObservedField(element_id="field_password", label="Password", field_type="text"),
        ],
        buttons=[ObservedAction(element_id="button_11", label="Sign in", kind="submit")],
    )
    prior_click = ActionTrace(
        observation=observation,
        proposed_action=ProposedAction(
            action_type="click",
            element_id="button_11",
            confidence=0.97,
            risk="low",
            reason="Submit the login form.",
            source="page",
        ),
        policy_decision="allowed",
        result=ActionResult(
            ok=True,
            action_type="click",
            element_id="button_11",
            message="action executed",
            navigated=False,
            new_url="https://ats.example/login",
            errors=["Please enter a valid password."],
        ),
    )
    generic_resume_trace = ActionTrace(
        observation=observation,
        proposed_action=ProposedAction(
            action_type="ask_user",
            question="The page did not advance after clicking Sign in. Review the page and continue when it is ready.",
            confidence=1.0,
            risk="medium",
            reason="User resumed after reviewing the unchanged login page.",
            source="user",
        ),
        policy_decision="allowed",
        result=ActionResult(
            ok=True,
            action_type="ask_user",
            message="User confirmed they reviewed the page.",
            value_after="true",
            new_url="https://ats.example/login",
        ),
    )

    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return observation

    async def planner(_settings: Any, **_kwargs: Any) -> ProposedAction:
        return ProposedAction(
            action_type="click",
            element_id="button_11",
            confidence=0.96,
            risk="low",
            reason="Submit the login form.",
            source="page",
        )

    def policy(**_kwargs: Any) -> PolicyDecision:
        raise AssertionError("policy should not be called for a stale repeated click")

    async def execute(_client: Any, _session_key: str, _action: ProposedAction) -> ActionResult:
        raise AssertionError("execute should not be called for a stale repeated click")

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={},
        recent_actions=[prior_click, generic_resume_trace],
        observe_fn=observe,
        planner_fn=planner,
        policy_fn=policy,
        execute_fn=execute,
    )

    assert state.status == "paused_for_user"
    assert state.pending_user_question is not None
    assert state.pending_user_question.target_element_id is None
    assert "Please enter a valid password." in state.pending_user_question.context


async def test_run_external_apply_step_does_not_execute_when_policy_pauses() -> None:
    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return PageObservation(
            url="https://ats.example/apply",
            page_type="form",
            fields=[ObservedField(element_id="field_salary", label="Expected salary", field_type="text")],
        )

    async def planner(_settings: Any, **_kwargs: Any) -> ProposedAction:
        return ProposedAction(
            action_type="fill_text",
            element_id="field_salary",
            value="150000",
            confidence=0.95,
            risk="low",
            reason="Salary matched memory.",
            source="memory",
        )

    def policy(**_kwargs: Any) -> PolicyDecision:
        return PolicyDecision(
            decision="paused",
            reason="Sensitive field.",
            pause_reason="sensitive",
            risk_flags=["salary"],
        )

    async def execute(_client: Any, _session_key: str, _action: ProposedAction) -> ActionResult:
        raise AssertionError("execute should not be called")

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={},
        observe_fn=observe,
        planner_fn=planner,
        policy_fn=policy,
        execute_fn=execute,
    )

    assert state.status == "paused_for_user"
    assert state.pending_user_question is not None
    assert state.risk_flags == ["salary"]
    assert state.completed_actions[0].policy_decision == "paused"
    assert state.completed_actions[0].result is None


async def test_run_external_apply_step_coerces_source_select_to_first_available_option() -> None:
    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return PageObservation(
            url="https://ats.example/apply",
            page_type="form",
            fields=[
                ObservedField(
                    element_id="field_source",
                    label="How did you hear about this role?",
                    field_type="select",
                    required=True,
                    options=["LinkedIn", "Company website", "Referral"],
                )
            ],
        )

    async def planner(_settings: Any, **_kwargs: Any) -> ProposedAction:
        return ProposedAction(
            action_type="select_option",
            element_id="field_source",
            value="SEEK",
            confidence=0.95,
            risk="low",
            reason="Configured default source for job applications.",
            source="profile",
        )

    def policy(**_kwargs: Any) -> PolicyDecision:
        return PolicyDecision(decision="allowed", reason="safe")

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        assert action.action_type == "select_option"
        assert action.value == "LinkedIn"
        assert "first safe available option" in action.reason
        return ActionResult(
            ok=True,
            action_type="select_option",
            element_id=action.element_id,
            value_after=action.value,
            new_url="https://ats.example/apply",
        )

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={},
        observe_fn=observe,
        planner_fn=planner,
        policy_fn=policy,
        execute_fn=execute,
    )

    assert state.status == "running"
    assert state.last_action_result is not None
    assert state.last_action_result.ok is True


async def test_run_external_apply_step_marks_reject_as_failed_without_execute() -> None:
    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return PageObservation(url="https://ats.example/apply")

    async def planner(_settings: Any, **_kwargs: Any) -> ProposedAction:
        return ProposedAction(
            action_type="click",
            element_id="button_missing",
            confidence=0.95,
            risk="low",
            reason="Click missing button.",
            source="page",
        )

    def policy(**_kwargs: Any) -> PolicyDecision:
        return PolicyDecision(
            decision="rejected",
            reason="Unknown element.",
            risk_flags=["unknown_element"],
        )

    async def execute(_client: Any, _session_key: str, _action: ProposedAction) -> ActionResult:
        raise AssertionError("execute should not be called")

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={},
        observe_fn=observe,
        planner_fn=planner,
        policy_fn=policy,
        execute_fn=execute,
    )

    assert state.status == "failed"
    assert state.error == "Unknown element."
    assert state.completed_actions[0].policy_decision == "rejected"


async def test_run_external_apply_step_auto_checks_standard_privacy_consent() -> None:
    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return PageObservation(
            url="https://secure.dc2.pageuppeople.com/apply",
            page_type="form",
            visible_text="You must consent to your personal data being handled according to the Privacy Statement.",
            fields=[
                ObservedField(
                    element_id="field_privacy",
                    label="Privacy Statement consent",
                    field_type="checkbox",
                    required=True,
                    nearby_text="I consent to PageUp processing and storing my personal data for this application.",
                )
            ],
        )

    async def planner(_settings: Any, **_kwargs: Any) -> ProposedAction:
        return ProposedAction(
            action_type="ask_user",
            element_id="field_privacy",
            question="Do you consent?",
            confidence=0.95,
            risk="medium",
            reason="Privacy consent.",
            source="page",
        )

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        assert action.action_type == "set_checkbox"
        assert action.value == "true"
        assert action.source == "user"
        return ActionResult(
            ok=True,
            action_type=action.action_type,
            element_id=action.element_id,
            value_after="checked",
            new_url="https://secure.dc2.pageuppeople.com/apply",
        )

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={},
        observe_fn=observe,
        planner_fn=planner,
        execute_fn=execute,
    )

    assert state.status == "running"
    assert state.pending_user_question is None
    assert state.completed_actions[-1].proposed_action.action_type == "set_checkbox"


async def test_run_external_apply_step_auto_checks_required_terms_consent() -> None:
    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return PageObservation(
            url="https://workday.example/apply",
            page_type="form",
            visible_text="You must accept the terms and conditions before continuing your application.",
            fields=[
                ObservedField(
                    element_id="field_terms",
                    label="I agree to the terms and conditions",
                    field_type="checkbox",
                    required=True,
                    nearby_text="Required to continue the application.",
                )
            ],
        )

    async def planner(_settings: Any, **_kwargs: Any) -> ProposedAction:
        return ProposedAction(
            action_type="ask_user",
            element_id="field_terms",
            question="Do you agree to the terms and conditions?",
            confidence=0.95,
            risk="medium",
            reason="Terms acceptance is required before continuing.",
            source="page",
        )

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        assert action.action_type == "set_checkbox"
        assert action.value == "true"
        assert action.source == "user"
        return ActionResult(
            ok=True,
            action_type=action.action_type,
            element_id=action.element_id,
            value_after="checked",
            new_url="https://workday.example/apply",
        )

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={},
        observe_fn=observe,
        planner_fn=planner,
        execute_fn=execute,
    )

    assert state.status == "running"
    assert state.pending_user_question is None
    assert state.completed_actions[-1].proposed_action.action_type == "set_checkbox"


async def test_run_external_apply_step_auto_checks_required_general_consent() -> None:
    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return PageObservation(
            url="https://jobs.example/apply",
            page_type="form",
            visible_text="You must acknowledge and agree before continuing your application.",
            fields=[
                ObservedField(
                    element_id="field_ack",
                    label="I acknowledge and agree to continue with my application",
                    field_type="checkbox",
                    required=True,
                    nearby_text="Required acknowledgement before continuing.",
                )
            ],
        )

    async def planner(_settings: Any, **_kwargs: Any) -> ProposedAction:
        return ProposedAction(
            action_type="ask_user",
            element_id="field_ack",
            question="Do you acknowledge and agree?",
            confidence=0.95,
            risk="medium",
            reason="Required acknowledgement before continuing.",
            source="page",
        )

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        assert action.action_type == "set_checkbox"
        assert action.value == "true"
        assert action.source == "user"
        return ActionResult(
            ok=True,
            action_type=action.action_type,
            element_id=action.element_id,
            value_after="checked",
            new_url="https://jobs.example/apply",
        )

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={},
        observe_fn=observe,
        planner_fn=planner,
        execute_fn=execute,
    )

    assert state.status == "running"
    assert state.pending_user_question is None
    assert state.completed_actions[-1].proposed_action.action_type == "set_checkbox"


async def test_run_external_apply_step_auto_checks_required_create_account_terms_consent() -> None:
    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return PageObservation(
            url="https://company.wd3.myworkdayjobs.com/apply",
            page_type="form",
            visible_text="Agree to the terms and conditions to create your account and continue.",
            fields=[
                ObservedField(
                    element_id="field_account_terms",
                    label="I agree to create an account and accept the Terms and Conditions",
                    field_type="checkbox",
                    required=True,
                    nearby_text="Required to create your candidate account and continue the application.",
                )
            ],
        )

    async def planner(_settings: Any, **_kwargs: Any) -> ProposedAction:
        return ProposedAction(
            action_type="ask_user",
            element_id="field_account_terms",
            question="Do you agree to the terms and conditions?",
            confidence=0.95,
            risk="medium",
            reason="Required account-creation consent before continuing.",
            source="page",
        )

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        assert action.action_type == "set_checkbox"
        assert action.value == "true"
        assert action.source == "user"
        return ActionResult(
            ok=True,
            action_type=action.action_type,
            element_id=action.element_id,
            value_after="checked",
            new_url="https://company.wd3.myworkdayjobs.com/apply",
        )

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={},
        observe_fn=observe,
        planner_fn=planner,
        execute_fn=execute,
    )

    assert state.status == "running"
    assert state.pending_user_question is None
    assert state.completed_actions[-1].proposed_action.action_type == "set_checkbox"


async def test_run_external_apply_step_auto_checks_optional_consent_when_configured_always_true() -> None:
    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return PageObservation(
            url="https://ats.example/apply",
            page_type="form",
            visible_text="Stay connected for future opportunities and marketing updates.",
            fields=[
                ObservedField(
                    element_id="field_marketing",
                    label="I agree to receive marketing and future opportunity emails",
                    field_type="checkbox",
                    required=True,
                    nearby_text="Privacy policy applies.",
                )
            ],
        )

    async def planner(_settings: Any, **_kwargs: Any) -> ProposedAction:
        return ProposedAction(
            action_type="ask_user",
            element_id="field_marketing",
            question="Do you agree to receive marketing emails?",
            confidence=0.95,
            risk="medium",
            reason="Optional marketing consent is present on the page.",
            source="page",
        )

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        assert action.action_type == "set_checkbox"
        assert action.value == "true"
        assert action.source == "user"
        return ActionResult(
            ok=True,
            action_type=action.action_type,
            element_id=action.element_id,
            value_after="checked",
            new_url="https://ats.example/apply",
        )

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={"external_accounts": {"always_accept_consents": True}},
        observe_fn=observe,
        planner_fn=planner,
        execute_fn=execute,
    )

    assert state.status == "running"
    assert state.pending_user_question is None
    assert state.completed_actions[-1].proposed_action.action_type == "set_checkbox"


async def test_apply_external_user_answer_checks_confirmed_checkbox() -> None:
    observation = PageObservation(
        url="https://secure.dc2.pageuppeople.com/apply",
        page_type="form",
        fields=[
            ObservedField(
                element_id="field_privacy",
                label="Privacy consent",
                field_type="checkbox",
                required=True,
            )
        ],
    )
    state = ExternalApplyState(
        application_id="app-1",
        current_url=observation.url,
        page_type="form",
        observation=observation,
        proposed_action=ProposedAction(
            action_type="ask_user",
            element_id="field_privacy",
            question="Do you consent?",
            confidence=0.92,
            risk="medium",
            reason="Legal consent requires user confirmation.",
            source="page",
        ),
        pending_user_question={
            "question": "Do you consent?",
            "context": "Legal consent requires user confirmation.",
            "target_element_id": "field_privacy",
        },
        status="paused_for_user",
    )

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        assert action.action_type == "set_checkbox"
        assert action.element_id == "field_privacy"
        assert action.value == "true"
        assert action.source == "user"
        return ActionResult(
            ok=True,
            action_type=action.action_type,
            element_id=action.element_id,
            value_after="checked",
            new_url=observation.url,
        )

    updated = await apply_external_user_answer(
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        external_state=state,
        answer="true",
        execute_fn=execute,
    )

    assert updated.status == "running"
    assert updated.pending_user_question is None
    assert updated.last_action_result is not None
    assert updated.last_action_result.ok is True
    assert updated.completed_actions[-1].proposed_action.action_type == "set_checkbox"


async def test_apply_external_user_answer_saves_field_answer_to_question_cache() -> None:
    cache = FakeQuestionCache()
    observation = PageObservation(
        url="https://ats.example/apply",
        page_type="screening_questions",
        fields=[
            ObservedField(
                element_id="field_notice",
                label="What is your notice period?",
                field_type="text",
                required=True,
            )
        ],
    )
    state = ExternalApplyState(
        application_id="app-1",
        current_url=observation.url,
        page_type="screening_questions",
        observation=observation,
        pending_user_question=UserQuestion(
            question="How should I answer: What is your notice period?",
            target_element_id="field_notice",
        ),
        status="paused_for_user",
    )

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        return ActionResult(
            ok=True,
            action_type=action.action_type,
            element_id=action.element_id,
            value_after=action.value,
            new_url=observation.url,
        )

    updated = await apply_external_user_answer(
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        external_state=state,
        answer="4 weeks",
        question_cache=cache,  # type: ignore[arg-type]
        execute_fn=execute,
    )

    assert updated.status == "running"
    assert cache.saved == [("What is your notice period?", "4 weeks", "text", "human_external")]


async def test_apply_external_user_answer_does_not_save_password_to_question_cache() -> None:
    cache = FakeQuestionCache()
    observation = PageObservation(
        url="https://ats.example/login",
        page_type="login",
        fields=[
            ObservedField(
                element_id="field_password",
                label="Password",
                field_type="password",
                required=True,
            )
        ],
    )
    state = ExternalApplyState(
        application_id="app-1",
        current_url=observation.url,
        page_type="login",
        observation=observation,
        pending_user_question=UserQuestion(
            question="How should I answer: Password?",
            target_element_id="field_password",
        ),
        status="paused_for_user",
    )

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        return ActionResult(
            ok=True,
            action_type=action.action_type,
            element_id=action.element_id,
            value_after=action.value,
            new_url=observation.url,
        )

    updated = await apply_external_user_answer(
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        external_state=state,
        answer="new-password",
        question_cache=cache,  # type: ignore[arg-type]
        execute_fn=execute,
    )

    assert updated.status == "running"
    assert cache.saved == []


async def test_apply_external_user_answers_binds_indirect_yes_no_question_to_radio_group() -> None:
    observation = PageObservation(
        url="https://secure.dc2.pageuppeople.com/apply",
        page_type="screening_questions",
        fields=[
            ObservedField(element_id="field_6", label="Yes", field_type="checkbox"),
            ObservedField(element_id="field_7", label="No", field_type="checkbox"),
            ObservedField(
                element_id="field_8",
                label=(
                    "Have you received a TVSP and separated from the South Australian "
                    "Public Sector in the last three years?*"
                ),
                field_type="radio",
                control_kind="native_radio_group",
                required=True,
                options=["Yes", "No"],
            ),
        ],
    )
    question = UserQuestion(
        question=(
            "Have you received a Targeted Voluntary Separation Package (TVSP) and separated "
            "from the South Australian Public Sector in the last three years? Please answer Yes or No."
        ),
        context="This is a required eligibility question and there is no approved fact or memory.",
        question_key="question-indirect_tvsp",
    )
    state = ExternalApplyState(
        application_id="app-1",
        current_url=observation.url,
        page_type="screening_questions",
        observation=observation,
        pending_user_question=question,
        pending_user_questions=[question],
        status="paused_for_user",
    )

    executed: list[ProposedAction] = []

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        executed.append(action)
        return ActionResult(
            ok=True,
            action_type=action.action_type,
            element_id=action.element_id,
            value_after=action.value,
            new_url=observation.url,
        )

    updated = await apply_external_user_answers(
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        external_state=state,
        answers_by_element_id={},
        answers_by_question_key={"question-indirect_tvsp": "No"},
        execute_fn=execute,
    )

    assert updated.status == "running"
    assert updated.pending_user_question is None
    assert updated.pending_user_questions == []
    assert len(executed) == 1
    assert executed[0].action_type == "set_radio"
    assert executed[0].element_id == "field_8"
    assert executed[0].value == "No"
    assert updated.completed_actions[-1].proposed_action.source == "user"


async def test_run_external_apply_step_reuses_recent_user_answer_when_field_id_changes() -> None:
    previous_observation = PageObservation(
        url="https://ats.example/apply",
        page_type="form",
        fields=[
            ObservedField(
                element_id="field_old",
                label="Expected salary",
                field_type="text",
            )
        ],
    )
    recent_user_answer = ActionTrace(
        observation=previous_observation,
        proposed_action=ProposedAction(
            action_type="fill_text",
            element_id="field_old",
            value="120000",
            confidence=1.0,
            risk="medium",
            reason="User explicitly answered the paused external-apply question.",
            source="user",
        ),
        policy_decision="allowed",
        result=ActionResult(
            ok=True,
            action_type="fill_text",
            element_id="field_old",
            value_after="120000",
            new_url="https://ats.example/apply",
        ),
    )

    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return PageObservation(
            url="https://ats.example/apply",
            page_type="form",
            fields=[
                ObservedField(
                    element_id="field_new",
                    label="Expected salary",
                    field_type="text",
                    required=True,
                )
            ],
        )

    async def planner(_settings: Any, **kwargs: Any) -> ProposedAction:
        approved_memory = kwargs["approved_memory"]
        assert any(
            item.get("label") == "Expected salary" and item.get("answer") == "120000"
            for item in approved_memory
        )
        return ProposedAction(
            action_type="fill_text",
            element_id="field_new",
            value="120000",
            confidence=0.95,
            risk="medium",
            reason="Reuse the approved user answer for the same field label.",
            source="memory",
        )

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        assert action.action_type == "fill_text"
        assert action.element_id == "field_new"
        assert action.value == "120000"
        return ActionResult(
            ok=True,
            action_type="fill_text",
            element_id="field_new",
            value_after="120000",
            new_url="https://ats.example/apply",
        )

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={},
        recent_actions=[recent_user_answer],
        observe_fn=observe,
        planner_fn=planner,
        execute_fn=execute,
    )

    assert state.status == "running"
    assert state.pending_user_question is None
    assert state.completed_actions[-1].proposed_action.source == "memory"


async def test_run_external_apply_step_uses_memory_context_to_create_account_after_rejected_login() -> None:
    login_observation = PageObservation(
        url="https://secure.workforceready.com.au/apply/login",
        page_type="login",
        errors=["Invalid username or password."],
        fields=[
            ObservedField(
                element_id="field_email",
                label="Username",
                field_type="text",
                required=True,
                current_value="candidate@example.com",
            ),
            ObservedField(
                element_id="field_password",
                label="Password",
                field_type="password",
                required=True,
                invalid=True,
                validation_message="Password is marked invalid.",
            ),
        ],
        buttons=[
            ObservedAction(element_id="button_login", label="Login", kind="submit"),
            ObservedAction(element_id="button_create", label="Create a new account", kind="button"),
        ],
    )
    password_filled = ActionTrace(
        observation=login_observation,
        proposed_action=ProposedAction(
            action_type="fill_text",
            element_id="field_password",
            value="default-password",
            confidence=0.95,
            risk="low",
            reason="Default external password.",
            source="profile",
        ),
        policy_decision="allowed",
        result=ActionResult(
            ok=True,
            action_type="fill_text",
            element_id="field_password",
            value_after="default-password",
            new_url=login_observation.url,
        ),
    )
    login_clicked = ActionTrace(
        observation=login_observation,
        proposed_action=ProposedAction(
            action_type="click",
            element_id="button_login",
            confidence=0.9,
            risk="medium",
            reason="Try saved login.",
            source="page",
        ),
        policy_decision="allowed",
        result=ActionResult(
            ok=True,
            action_type="click",
            element_id="button_login",
            navigated=True,
            new_url=login_observation.url,
            errors=["Invalid username or password."],
        ),
    )

    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return login_observation

    async def planner(_settings: Any, **_kwargs: Any) -> ProposedAction:
        raise AssertionError("memory-context account routing should run before the planner")

    def policy(**_kwargs: Any) -> PolicyDecision:
        return PolicyDecision(decision="allowed", reason="safe")

    executed: list[str | None] = []

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        executed.append(action.element_id)
        return ActionResult(
            ok=True,
            action_type=action.action_type,
            element_id=action.element_id,
            new_url="https://secure.workforceready.com.au/apply/create-account",
            navigated=True,
        )

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={
            "external_accounts": {
                "default": {"email": "candidate@example.com", "password": "default-password"}
            }
        },
        recent_actions=[password_filled, login_clicked],
        observe_fn=observe,
        planner_fn=planner,
        policy_fn=policy,
        execute_fn=execute,
    )

    assert executed == ["button_create"]
    assert state.memory_context is not None
    assert state.memory_context.saved_login_rejected is True
    assert state.memory_context.portal_identity == "secure.workforceready.com.au"
    assert state.memory_context.account_email == "candidate@example.com"
    assert state.memory_context.credential_available is True
    assert state.memory_context.credential_status == "rejected"
    assert state.completed_actions[-1].proposed_action.source == "memory"


async def test_run_external_apply_step_passes_portal_scoped_account_memory_to_planner() -> None:
    observation = PageObservation(
        url="https://tenant.workdayjobs.com/apply/login",
        title="Candidate Home",
        page_type="login",
        fields=[ObservedField(element_id="field_email", label="Email", field_type="email")],
    )

    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return observation

    async def planner(_settings: Any, **kwargs: Any) -> ProposedAction:
        memory_context = kwargs["memory_context"]
        assert memory_context["portal_host"] == "tenant.workdayjobs.com"
        assert memory_context["account_status"] == "created"
        assert memory_context["account_email"] == "portal@example.com"
        assert memory_context["credential_available"] is True
        assert memory_context["credential_status"] == "verified"
        assert any("created" in item for item in memory_context["recommendations"])
        return ProposedAction(
            action_type="ask_user",
            question="pause",
            confidence=0.9,
            risk="medium",
            reason="test",
            source="page",
        )

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={
            "external_accounts": {
                "portals": {
                    "tenant.workdayjobs.com": {
                        "status": "created",
                        "email": "portal@example.com",
                        "password": "portal-password",
                        "credential_status": "verified",
                    }
                }
            }
        },
        observe_fn=observe,
        planner_fn=planner,
    )

    assert state.status == "paused_for_user"


async def test_run_external_apply_step_coerces_login_stop_ready_to_sign_in_click() -> None:
    observation = PageObservation(
        url="https://secure.workforceready.com.au/apply/login",
        title="Sign In / Register - Job Candidate Account",
        page_type="login",
        fields=[
            ObservedField(
                element_id="field_username",
                label="Username",
                field_type="text",
                required=True,
                current_value="candidate@example.com",
            ),
            ObservedField(
                element_id="field_password",
                label="Password",
                field_type="password",
                required=True,
                current_value="saved-password",
            ),
        ],
        buttons=[
            ObservedAction(element_id="button_signin", label="Sign In", kind="submit"),
            ObservedAction(element_id="button_create", label="Create a new account", kind="submit"),
        ],
    )

    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return observation

    async def planner(_settings: Any, **_kwargs: Any) -> ProposedAction:
        return ProposedAction(
            action_type="stop_ready_to_submit",
            confidence=0.99,
            risk="low",
            reason="Mistakenly treated Sign In as final submit.",
            source="page",
        )

    executed: list[ProposedAction] = []

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        executed.append(action)
        return ActionResult(
            ok=True,
            action_type=action.action_type,
            element_id=action.element_id,
            navigated=True,
            new_url="https://secure.workforceready.com.au/apply/profile",
        )

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={},
        observe_fn=observe,
        planner_fn=planner,
        execute_fn=execute,
    )

    assert [action.action_type for action in executed] == ["click"]
    assert executed[0].element_id == "button_signin"
    assert state.status == "running"
    assert state.submit_ready is False


async def test_run_external_apply_step_does_not_treat_combobox_transcript_as_substantive_error() -> None:
    observation = PageObservation(
        url="https://secure.dc2.pageuppeople.com/apply/check",
        page_type="form",
        errors=[
            "Are you currently authorised to work in Australia?* Open list Selected: "
            "Yes - I am a permanent resident / citizen Select Yes - I am a permanent resident / citizen "
            "Yes - I have a current work permit / visa No - I require sponsorship "
            "SetupConditionalAttributeItems(9575, 28683, 1, 9574); aAttributeItems[9574] = aC"
        ],
        fields=[
            ObservedField(
                element_id="field_rights",
                label="Are you currently authorised to work in Australia?*",
                field_type="select",
                current_value="Yes - I am a permanent resident / citizen",
                required=True,
                options=[
                    "Select",
                    "Yes - I am a permanent resident / citizen",
                    "Yes - I have a current work permit / visa",
                    "No - I require sponsorship",
                ],
            )
        ],
        buttons=[ObservedAction(element_id="button_continue", label="Continue", kind="submit")],
    )
    prior_click = ActionTrace(
        observation=observation,
        proposed_action=ProposedAction(
            action_type="click",
            element_id="button_continue",
            confidence=0.95,
            risk="low",
            reason="Continue.",
            source="page",
        ),
        policy_decision="allowed",
        result=ActionResult(
            ok=True,
            action_type="click",
            element_id="button_continue",
            navigated=False,
            new_url=observation.url,
            errors=observation.errors,
        ),
    )

    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return observation

    async def planner(_settings: Any, **_kwargs: Any) -> ProposedAction:
        return ProposedAction(
            action_type="click",
            element_id="button_continue",
            confidence=0.95,
            risk="low",
            reason="Required fields have useful values.",
            source="page",
        )

    def policy(**_kwargs: Any) -> PolicyDecision:
        return PolicyDecision(decision="allowed", reason="safe")

    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={},
        recent_actions=[prior_click],
        observe_fn=observe,
        planner_fn=planner,
        policy_fn=policy,
        sleep_fn=sleep,
    )

    assert sleeps == [1.0, 2.0, 4.0]
    assert state.status == "paused_for_user"
    assert state.pending_user_question is not None
    assert "page did not advance" in state.pending_user_question.question.lower()


async def test_run_external_apply_step_passes_recent_executor_failure_diagnostics_to_planner() -> None:
    observation = PageObservation(
        url="https://secure.dc2.pageuppeople.com/apply/check",
        page_type="form",
        fields=[
            ObservedField(
                element_id="field_1",
                label="Are you currently authorised to work in Australia?",
                field_type="text",
                current_value="Select",
            )
        ],
    )
    failed_trace = ActionTrace(
        observation=observation,
        proposed_action=ProposedAction(
            action_type="select_option",
            element_id="field_1",
            value="Yes - I am a permanent resident / citizen",
            confidence=0.99,
            risk="low",
            reason="Use work-rights profile fact.",
            source="profile",
        ),
        policy_decision="allowed",
        result=ActionResult(
            ok=False,
            action_type="select_option",
            element_id="field_1",
            message='No combobox option matching "Yes - I am a permanent resident / citizen"',
            diagnostics={
                "control_role": "combobox",
                "requested_value": "Yes - I am a permanent resident / citizen",
                "initial_options": [],
                "visible_errors_before": ["Are you currently authorised to work in Australia? Yes - I am a permanent resident / citizen"],
                "page_url_before": "https://secure.dc2.pageuppeople.com/apply/check",
            },
        ),
    )

    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return observation

    async def planner(_settings: Any, **kwargs: Any) -> ProposedAction:
        failures = kwargs["memory_context"]["recent_failures"]
        assert failures[0]["action_type"] == "select_option"
        assert failures[0]["field_label"] == "Are you currently authorised to work in Australia?"
        assert failures[0]["diagnostics"]["control_role"] == "combobox"
        assert failures[0]["diagnostics"]["requested_value"] == "Yes - I am a permanent resident / citizen"
        assert "page_url_before" not in failures[0]["diagnostics"]
        return ProposedAction(
            action_type="ask_user",
            question="pause",
            confidence=0.9,
            risk="medium",
            reason="test",
            source="page",
        )

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={},
        recent_actions=[failed_trace],
        observe_fn=observe,
        planner_fn=planner,
    )

    assert state.status == "paused_for_user"


async def test_run_external_apply_step_feeds_question_cache_answers_to_planner() -> None:
    cache = FakeQuestionCache(found="Yes")

    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return PageObservation(
            url="https://ats.example/apply",
            page_type="screening_questions",
            fields=[
                ObservedField(
                    element_id="field_rights",
                    label="Do you have unrestricted work rights in Australia?",
                    field_type="radio",
                    required=True,
                    options=["Yes", "No"],
                    nearby_text="Do you have unrestricted work rights in Australia? Yes No",
                )
            ],
        )

    async def planner(_settings: Any, **kwargs: Any) -> ProposedAction:
        assert kwargs["memory_context"]["portal_host"] == "ats.example"
        assert kwargs["planning_frame"]["phase"] == "screening"
        assert any(
            item.get("label") == "Do you have unrestricted work rights in Australia?"
            and item.get("answer") == "Yes"
            and item.get("source") == "question_answer_cache"
            and item.get("portal_host") == "ats.example"
            and item.get("options") == ["Yes", "No"]
            and item.get("option_signature") == "yes|no"
            and item.get("question_fingerprint")
            for item in kwargs["approved_memory"]
        )
        return ProposedAction(
            action_type="set_radio",
            element_id="field_rights",
            value="Yes",
            confidence=0.95,
            risk="medium",
            reason="Use approved answer memory.",
            source="memory",
        )

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        return ActionResult(
            ok=True,
            action_type=action.action_type,
            element_id=action.element_id,
            value_after=action.value,
            new_url="https://ats.example/apply",
        )

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={},
        question_cache=cache,  # type: ignore[arg-type]
        observe_fn=observe,
        planner_fn=planner,
        execute_fn=execute,
    )

    assert state.status == "running"
    assert state.completed_actions[-1].proposed_action.source == "memory"


async def test_run_external_apply_step_lets_planner_choose_manual_entry_on_profile_entry_choice_page() -> None:
    observation = PageObservation(
        url="https://secure.workforceready.com.au/apply",
        page_type="unknown",
        visible_text=(
            "Apply for Job Principal Data Engineer Hello, let's start your application "
            "What's the best way to get your info? Use my CV Type it in myself Applied here before? Log in"
        ),
        buttons=[
            ObservedAction(element_id="button_cv", label="Use my CV", kind="button"),
            ObservedAction(element_id="button_manual", label="Type it in myself", kind="button"),
            ObservedAction(element_id="button_login", label="Log in", kind="submit"),
        ],
    )

    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return observation

    planner_observations: list[PageObservation] = []

    async def planner(_settings: Any, **kwargs: Any) -> ProposedAction:
        planner_observations.append(kwargs["observation"])
        return ProposedAction(
            action_type="click",
            element_id="button_manual",
            confidence=0.92,
            risk="medium",
            reason="Planner chose manual entry over CV parsing for a more observable application flow.",
            source="page",
        )

    def policy(**_kwargs: Any) -> PolicyDecision:
        return PolicyDecision(decision="allowed", reason="safe")

    executed: list[str | None] = []

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        executed.append(action.element_id)
        return ActionResult(
            ok=True,
            action_type=action.action_type,
            element_id=action.element_id,
            navigated=True,
            new_url="https://secure.workforceready.com.au/apply/manual",
        )

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={},
        observe_fn=observe,
        planner_fn=planner,
        policy_fn=policy,
        execute_fn=execute,
    )

    assert executed == ["button_manual"]
    assert planner_observations == [observation]
    assert state.status == "running"
    assert state.completed_actions[-1].proposed_action.source == "page"


async def test_apply_external_user_answers_records_generic_consent_approval() -> None:
    observation = PageObservation(
        url="https://workday.example/login",
        page_type="login",
        visible_text="A required consent appears to exist from the page text.",
    )
    question = UserQuestion(
        question="Please confirm whether it should be accepted so the workflow can continue when that control is available.",
        context="A required consent appears to exist from the page text, but there is no observed element_id for the checkbox.",
        question_key="question-indirect_consent",
    )
    state = ExternalApplyState(
        application_id="app-1",
        current_url=observation.url,
        page_type="login",
        observation=observation,
        pending_user_question=question,
        pending_user_questions=[question],
        status="paused_for_user",
    )

    updated = await apply_external_user_answers(
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        external_state=state,
        answers_by_element_id={},
        answers_by_question_key={"question-indirect_consent": "true"},
    )

    assert updated.status == "running"
    assert updated.pending_user_question is None
    assert updated.pending_user_questions == []
    assert updated.completed_actions[-1].result is not None
    assert updated.completed_actions[-1].result.ok is True


async def test_apply_external_user_answers_records_generic_review_acknowledgement() -> None:
    observation = PageObservation(
        url="https://ats.example/login",
        page_type="login",
        visible_text="Create Account",
    )
    question = UserQuestion(
        question="The page did not advance after clicking Create Account. Review the page and continue when it is ready.",
        context="The page stayed on the same step after clicking Create Account. Please review any highlighted errors or missing fields, then continue when the page is ready.",
        question_key="question-indirect_review",
    )
    state = ExternalApplyState(
        application_id="app-1",
        current_url=observation.url,
        page_type="login",
        observation=observation,
        pending_user_question=question,
        pending_user_questions=[question],
        status="paused_for_user",
    )

    updated = await apply_external_user_answers(
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        external_state=state,
        answers_by_element_id={},
        answers_by_question_key={"question-indirect_review": "true"},
    )

    assert updated.status == "running"
    assert updated.pending_user_question is None
    assert updated.pending_user_questions == []
    assert updated.completed_actions[-1].result is not None
    assert updated.completed_actions[-1].result.ok is True


async def test_run_external_apply_step_uses_prior_generic_consent_approval_when_checkbox_appears() -> None:
    approved_prompt = ProposedAction(
        action_type="ask_user",
        question="Please confirm whether it should be accepted so the workflow can continue when that control is available.",
        confidence=0.95,
        risk="medium",
        reason="A required consent appears to exist from the page text, but there is no observed element_id for the checkbox.",
        source="user",
    )
    approved_trace = ActionTrace(
        observation=PageObservation(
            url="https://workday.example/login",
            page_type="login",
            visible_text="A required consent appears to exist from the page text.",
        ),
        proposed_action=approved_prompt,
        policy_decision="allowed",
        result=ActionResult(
            ok=True,
            action_type="ask_user",
            message="User approved the generic external-apply consent prompt.",
            value_after="true",
            new_url="https://workday.example/login",
        ),
    )

    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return PageObservation(
            url="https://workday.example/apply",
            page_type="form",
            visible_text="Please accept the terms and conditions before continuing.",
            fields=[
                ObservedField(
                    element_id="field_terms",
                    label="I accept the terms and conditions",
                    field_type="checkbox",
                    required=True,
                    nearby_text="Required to continue.",
                )
            ],
        )

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        assert action.action_type == "set_checkbox"
        assert action.element_id == "field_terms"
        assert action.value == "true"
        return ActionResult(
            ok=True,
            action_type="set_checkbox",
            element_id="field_terms",
            value_after="checked",
            new_url="https://workday.example/apply",
        )

    state = await run_external_apply_step(
        DummySettings(),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={},
        recent_actions=[approved_trace],
        observe_fn=observe,
        execute_fn=execute,
    )

    assert state.status == "running"
    assert state.completed_actions[-1].proposed_action.action_type == "set_checkbox"


def test_realign_external_state_to_observation_rebinds_pending_question_targets() -> None:
    original_observation = PageObservation(
        url="https://ats.example/apply/contact",
        page_type="form",
        fields=[
            ObservedField(
                element_id="field_9",
                label="Phone Device Type*",
                field_type="select",
                options=["Mobile", "Landline"],
            )
        ],
    )
    updated_observation = PageObservation(
        url="https://ats.example/apply/contact",
        page_type="form",
        fields=[
            ObservedField(
                element_id="field_23",
                label="Phone Device Type*",
                field_type="select",
                options=["Mobile", "Landline", "Work"],
            )
        ],
    )
    pending_question = UserQuestion(
        question="What should I select for: Phone Device Type*?",
        target_element_id="field_9",
        suggested_answers=["Mobile", "Landline"],
    )
    external_state = ExternalApplyState(
        application_id="app-1",
        observation=original_observation,
        pending_user_question=pending_question,
        pending_user_questions=[pending_question],
    )

    rebound = realign_external_state_to_observation(external_state, updated_observation)

    assert rebound.observation == updated_observation
    assert rebound.pending_user_question is not None
    assert rebound.pending_user_question.target_element_id == "field_23"
    assert rebound.pending_user_question.suggested_answers == ["Mobile", "Landline", "Work"]
