from app.services.external_apply_policy import validate_external_apply_action
from app.state.external_apply import ObservedAction, ObservedField, PageObservation, ProposedAction


def test_policy_allows_safe_profile_backed_fill() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[ObservedField(element_id="field_email", label="Email", field_type="email")],
    )
    action = ProposedAction(
        action_type="fill_text",
        element_id="field_email",
        value="nitin@example.com",
        confidence=0.94,
        risk="low",
        reason="Email comes from profile.",
        source="profile",
    )

    decision = validate_external_apply_action(
        observation=observation,
        proposed_action=action,
        profile_facts={"contact": {"email": "nitin@example.com"}},
    )

    assert decision.decision == "allowed"


def test_policy_allows_password_fill_from_external_accounts_default() -> None:
    observation = PageObservation(
        url="https://ats.example/login",
        fields=[ObservedField(element_id="field_password", label="Password", field_type="text")],
    )
    action = ProposedAction(
        action_type="fill_text",
        element_id="field_password",
        value="Sunshine@123#5",
        confidence=0.94,
        risk="low",
        reason="Password comes from the configured external accounts file.",
        source="profile",
    )

    decision = validate_external_apply_action(
        observation=observation,
        proposed_action=action,
        profile_facts={"external_accounts": {"default": {"password": "Sunshine@123#5"}}},
    )

    assert decision.decision == "allowed"


def test_policy_pauses_job_search_field_even_when_planner_claims_profile_source() -> None:
    observation = PageObservation(
        url="https://hk.jobsdb.com/",
        visible_text="Perform a job search What Suggestions will appear below the field as you type",
        fields=[ObservedField(element_id="field_what", label="What", field_type="text")],
    )
    action = ProposedAction(
        action_type="fill_text",
        element_id="field_what",
        value="AI Engineer",
        confidence=0.96,
        risk="low",
        reason="Profile is positioned for AI engineering.",
        source="profile",
    )

    decision = validate_external_apply_action(
        observation=observation,
        proposed_action=action,
        profile_facts={"headline": "AI Engineer"},
    )

    assert decision.decision == "paused"
    assert decision.pause_reason == "needs_approval"
    assert "job_search_field" in decision.risk_flags


def test_policy_pauses_profile_value_that_does_not_match_contact_fact() -> None:
    observation = PageObservation(
        url="https://secure.dc2.pageuppeople.com/apply",
        fields=[ObservedField(element_id="field_email", label="Email address", field_type="email")],
    )
    action = ProposedAction(
        action_type="fill_text",
        element_id="field_email",
        value="AI Engineer",
        confidence=0.96,
        risk="low",
        reason="Planner claimed this came from profile.",
        source="profile",
    )

    decision = validate_external_apply_action(
        observation=observation,
        proposed_action=action,
        profile_facts={"contact": {"email": "nitin@example.com"}},
    )

    assert decision.decision == "paused"
    assert decision.pause_reason == "needs_approval"
    assert "profile_value_mismatch" in decision.risk_flags


def test_policy_pauses_low_confidence_action() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[ObservedField(element_id="field_name", label="Name", field_type="text")],
    )
    action = ProposedAction(
        action_type="fill_text",
        element_id="field_name",
        value="Nitin Datta",
        confidence=0.6,
        risk="low",
        reason="Maybe this is the name field.",
        source="profile",
    )

    decision = validate_external_apply_action(observation=observation, proposed_action=action)

    assert decision.decision == "paused"
    assert decision.pause_reason == "low_confidence"


def test_policy_pauses_sensitive_salary_field_even_with_profile_source() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[ObservedField(element_id="field_salary", label="Expected salary", field_type="text")],
    )
    action = ProposedAction(
        action_type="fill_text",
        element_id="field_salary",
        value="150000",
        confidence=0.95,
        risk="low",
        reason="Salary matched memory.",
        source="memory",
    )

    decision = validate_external_apply_action(observation=observation, proposed_action=action)

    assert decision.decision == "paused"
    assert decision.pause_reason == "sensitive"
    assert "salary" in decision.risk_flags


def test_policy_allows_standard_required_privacy_consent_checkbox() -> None:
    observation = PageObservation(
        url="https://secure.dc2.pageuppeople.com/apply",
        visible_text="You must consent to your personal data being handled according to the Privacy Statement and transferred to Australia for processing.",
        fields=[
            ObservedField(
                element_id="field_privacy",
                label="I consent to the Privacy Statement",
                field_type="checkbox",
                required=True,
                nearby_text="Required. Personal data may be provided to PageUp and stored for this application.",
            )
        ],
    )
    action = ProposedAction(
        action_type="set_checkbox",
        element_id="field_privacy",
        value="true",
        confidence=0.98,
        risk="low",
        reason="Required privacy consent.",
        source="user",
    )

    decision = validate_external_apply_action(observation=observation, proposed_action=action)

    assert decision.decision == "allowed"


def test_policy_allows_required_create_account_terms_checkbox() -> None:
    observation = PageObservation(
        url="https://company.wd3.myworkdayjobs.com/apply",
        visible_text="You must agree to the terms and conditions to create your account and continue.",
        fields=[
            ObservedField(
                element_id="field_create_account_terms",
                label="I agree to create an account and accept the Terms and Conditions",
                field_type="checkbox",
                required=True,
                nearby_text="Required to create your candidate account and continue the application.",
            )
        ],
    )
    action = ProposedAction(
        action_type="set_checkbox",
        element_id="field_create_account_terms",
        value="true",
        confidence=0.98,
        risk="low",
        reason="Required account-creation terms consent.",
        source="user",
    )

    decision = validate_external_apply_action(observation=observation, proposed_action=action)

    assert decision.decision == "allowed"


def test_policy_pauses_optional_marketing_consent_checkbox() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
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
    action = ProposedAction(
        action_type="set_checkbox",
        element_id="field_marketing",
        value="true",
        confidence=0.98,
        risk="low",
        reason="Marketing consent.",
        source="user",
    )

    decision = validate_external_apply_action(observation=observation, proposed_action=action)

    assert decision.decision == "paused"
    assert decision.pause_reason == "needs_approval"


def test_policy_allows_optional_marketing_consent_when_configured_always_true() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
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
    action = ProposedAction(
        action_type="set_checkbox",
        element_id="field_marketing",
        value="true",
        confidence=0.98,
        risk="low",
        reason="Consent checkbox configured to default true.",
        source="user",
    )

    decision = validate_external_apply_action(
        observation=observation,
        proposed_action=action,
        profile_facts={"external_accounts": {"always_accept_consents": True}},
    )

    assert decision.decision == "allowed"


def test_policy_pauses_final_submit_click() -> None:
    observation = PageObservation(
        url="https://ats.example/review",
        buttons=[ObservedAction(element_id="button_submit", label="Submit application", kind="submit")],
    )
    action = ProposedAction(
        action_type="click",
        element_id="button_submit",
        confidence=0.96,
        risk="low",
        reason="Ready to submit.",
        source="page",
    )

    decision = validate_external_apply_action(observation=observation, proposed_action=action)

    assert decision.decision == "paused"
    assert decision.pause_reason == "final_submit"


def test_policy_allows_next_button_even_when_html_type_is_submit() -> None:
    observation = PageObservation(
        url="https://secure.dc2.pageuppeople.com/apply",
        buttons=[ObservedAction(element_id="button_next", label="Next", kind="submit", nearby_text="Next Cancel")],
    )
    action = ProposedAction(
        action_type="click",
        element_id="button_next",
        confidence=0.95,
        risk="low",
        reason="Proceed to the next application page.",
        source="page",
    )

    decision = validate_external_apply_action(observation=observation, proposed_action=action)

    assert decision.decision == "allowed"


def test_policy_accepts_canonical_street_address_as_profile_value() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[ObservedField(element_id="field_address", label="Home address", field_type="text", required=True)],
    )
    action = ProposedAction(
        action_type="fill_text",
        element_id="field_address",
        value="123 Example Street",
        confidence=0.96,
        risk="low",
        reason="Home address comes from the canonical profile.",
        source="profile",
    )

    decision = validate_external_apply_action(
        observation=observation,
        proposed_action=action,
        profile_facts={
            "address": {
                "street": "123 Example Street",
                "formatted": "123 Example Street, Sampleville NSW 2000, Australia",
            }
        },
    )

    assert decision.decision == "allowed"


def test_policy_accepts_resume_upload_from_configured_profile_file() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[ObservedField(element_id="field_resume", label="Resume", field_type="file", required=True)],
    )
    action = ProposedAction(
        action_type="upload_file",
        element_id="field_resume",
        value="C:/workspace/profile/example_resume.docx",
        confidence=0.96,
        risk="low",
        reason="Resume upload comes from the configured profile resume file.",
        source="profile",
    )

    decision = validate_external_apply_action(
        observation=observation,
        proposed_action=action,
        profile_facts={"resume_path": "C:/workspace/profile/example_resume.docx"},
    )

    assert decision.decision == "allowed"


def test_policy_rejects_unknown_element() -> None:
    observation = PageObservation(url="https://ats.example/apply")
    action = ProposedAction(
        action_type="click",
        element_id="button_missing",
        confidence=0.96,
        risk="low",
        reason="Click it.",
        source="page",
    )

    decision = validate_external_apply_action(observation=observation, proposed_action=action)

    assert decision.decision == "rejected"
    assert "unknown_element" in decision.risk_flags


def test_policy_pauses_planner_user_request() -> None:
    observation = PageObservation(url="https://ats.example/apply")
    action = ProposedAction(
        action_type="ask_user",
        question="How should I answer?",
        confidence=0.9,
        risk="medium",
        reason="User judgement required.",
        source="page",
    )

    decision = validate_external_apply_action(observation=observation, proposed_action=action)

    assert decision.decision == "paused"
    assert decision.pause_reason == "needs_user_input"
