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


def test_policy_pauses_select_option_when_target_is_not_an_observed_field() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        buttons=[ObservedAction(element_id="button_20", label="How did you hear about us?", kind="button")],
    )
    action = ProposedAction(
        action_type="select_option",
        element_id="button_20",
        value="SEEK",
        confidence=0.94,
        risk="low",
        reason="Configured default source for job applications.",
        source="profile",
    )

    decision = validate_external_apply_action(
        observation=observation,
        proposed_action=action,
        profile_facts={"external_accounts": {"default": {"heard_about": "SEEK"}}},
    )

    assert decision.decision == "paused"
    assert decision.pause_reason == "needs_user_input"
    assert "non_field_form_target" in decision.risk_flags


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


def test_policy_allows_high_risk_password_fill_when_it_matches_external_accounts_default() -> None:
    observation = PageObservation(
        url="https://ats.example/login",
        fields=[ObservedField(element_id="field_password", label="Password", field_type="password")],
    )
    action = ProposedAction(
        action_type="fill_text",
        element_id="field_password",
        value="configured-secret",
        confidence=0.94,
        risk="high",
        reason="Password comes from the configured external accounts file.",
        source="profile",
    )

    decision = validate_external_apply_action(
        observation=observation,
        proposed_action=action,
        profile_facts={"external_accounts": {"default": {"password": "configured-secret"}}},
    )

    assert decision.decision == "allowed"


def test_policy_allows_prior_employment_answer_from_profile_history() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[ObservedField(element_id="field_svha", label="Have you previously worked at St Vincent's Health Australia (SVHA)?", field_type="radio")],
    )
    action = ProposedAction(
        action_type="set_radio",
        element_id="field_svha",
        value="No",
        confidence=0.94,
        risk="low",
        reason="Derived from prior employer history.",
        source="profile",
    )

    decision = validate_external_apply_action(
        observation=observation,
        proposed_action=action,
        profile_facts={"employment_history": {"employers": ["AWS", "Department for Education, South Australia"]}},
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


def test_policy_allows_sensitive_salary_field_from_approved_memory() -> None:
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

    assert decision.decision == "allowed"


def test_policy_pauses_sensitive_salary_field_from_profile_source() -> None:
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
        reason="Salary matched profile.",
        source="profile",
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


def test_policy_allows_phone_device_type_from_external_accounts_default() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[ObservedField(element_id="field_device_type", label="What phone device type should be selected for your phone number?", field_type="select")],
    )
    action = ProposedAction(
        action_type="select_option",
        element_id="field_device_type",
        value="Mobile",
        confidence=0.98,
        risk="low",
        reason="Phone device type comes from configured defaults.",
        source="profile",
    )

    decision = validate_external_apply_action(
        observation=observation,
        proposed_action=action,
        profile_facts={"external_accounts": {"default": {"phone_device_type": "Mobile"}}},
    )

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


def test_policy_accepts_resume_upload_from_configured_profile_file(tmp_path) -> None:
    resume_path = tmp_path / "resume.docx"
    resume_path.write_bytes(b"docx")
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[ObservedField(element_id="field_resume", label="Resume", field_type="file", required=True)],
    )
    action = ProposedAction(
        action_type="upload_file",
        element_id="field_resume",
        value=str(resume_path).replace("\\", "/"),
        confidence=0.96,
        risk="low",
        reason="Resume upload comes from the configured profile resume file.",
        source="profile",
    )

    decision = validate_external_apply_action(
        observation=observation,
        proposed_action=action,
        profile_facts={"resume_path": str(resume_path)},
    )

    assert decision.decision == "allowed"


def test_policy_accepts_required_file_upload_on_resume_upload_page_with_noisy_label(tmp_path) -> None:
    resume_path = tmp_path / "resume.docx"
    resume_path.write_bytes(b"docx")
    observation = PageObservation(
        url="https://ats.example/apply#/step1",
        page_type="resume_upload",
        title="Upload Resume",
        visible_text="Your current resume must be uploaded in order to submit this application. Click Browse and Upload.",
        fields=[
            ObservedField(
                element_id="field_1",
                label=(
                    "var regexInvalidFilenameCharacters = '[?\\'\"\\:<>|]'; "
                    "Senior Data Innovation Lead Posted: 01/05/2026 Job Type: Permanent"
                ),
                field_type="file",
                control_kind="file_upload",
                required=False,
            )
        ],
        links=[
            ObservedAction(
                element_id="link_resume",
                label="Resume",
                kind="link",
                href="https://ats.example/apply",
                nearby_text="Resume Application Submit",
            )
        ],
    )
    action = ProposedAction(
        action_type="upload_file",
        element_id="field_1",
        value=str(resume_path),
        confidence=0.96,
        risk="low",
        reason="Resume upload comes from the configured profile resume file.",
        source="profile",
    )

    decision = validate_external_apply_action(
        observation=observation,
        proposed_action=action,
        profile_facts={"resume_path": str(resume_path)},
    )

    assert decision.decision == "allowed"


def test_policy_accepts_cover_letter_upload_from_generated_profile_file(tmp_path) -> None:
    cover_letter_path = tmp_path / "cover-letter.txt"
    cover_letter_path.write_text("Dear Hiring Team,\n\nI am excited to apply.\n", encoding="utf-8")
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[ObservedField(element_id="field_cover", label="Cover letter", field_type="file", required=False)],
    )
    action = ProposedAction(
        action_type="upload_file",
        element_id="field_cover",
        value=str(cover_letter_path).replace("\\", "/"),
        confidence=0.96,
        risk="low",
        reason="Cover letter upload comes from the generated application draft.",
        source="profile",
    )

    decision = validate_external_apply_action(
        observation=observation,
        proposed_action=action,
        profile_facts={"cover_letter_path": str(cover_letter_path)},
    )

    assert decision.decision == "allowed"


def test_policy_accepts_cover_letter_upload_mode_from_generated_profile_file(tmp_path) -> None:
    cover_letter_path = tmp_path / "cover-letter.txt"
    cover_letter_path.write_text("Dear Hiring Team,\n\nI am excited to apply.\n", encoding="utf-8")
    observation = PageObservation(
        url="https://ats.example/apply",
        page_type="resume_upload",
        fields=[
            ObservedField(
                element_id="field_cover_choice",
                label="No cover letter",
                field_type="radio",
                control_kind="native_radio_group",
                current_value="No cover letter",
                options=[
                    "No cover letter",
                    "Upload my cover letter from my computer",
                    "Write or paste my cover letter",
                ],
            )
        ],
    )
    action = ProposedAction(
        action_type="set_radio",
        element_id="field_cover_choice",
        value="Upload my cover letter from my computer",
        confidence=0.97,
        risk="low",
        reason="Choose the cover-letter upload mode because a generated cover-letter file exists.",
        source="profile",
    )

    decision = validate_external_apply_action(
        observation=observation,
        proposed_action=action,
        profile_facts={"cover_letter_path": str(cover_letter_path)},
    )

    assert decision.decision == "allowed"


def test_policy_accepts_ordered_noisy_resume_and_cover_letter_uploads(tmp_path) -> None:
    resume_path = tmp_path / "resume.docx"
    cover_letter_path = tmp_path / "cover-letter.txt"
    resume_path.write_bytes(b"docx")
    cover_letter_path.write_text("Dear Hiring Team,\n\nI am excited to apply.\n", encoding="utf-8")
    noisy_label = (
        "var regexInvalidFilenameCharacters = '[?\\'\"\\:<>|]'; "
        "Senior Data Innovation Lead - Copy Posted: 01/05/2026"
    )
    observation = PageObservation(
        url="https://ats.example/apply#/step1",
        page_type="resume_upload",
        fields=[
            ObservedField(
                element_id="field_resume",
                label=noisy_label,
                field_type="file",
                control_kind="file_upload",
                required=False,
            ),
            ObservedField(
                element_id="field_cover_choice",
                label="No cover letter",
                field_type="radio",
                control_kind="native_radio_group",
                current_value="Upload my cover letter from my computer",
                options=[
                    "No cover letter",
                    "Upload my cover letter from my computer",
                    "Write or paste my cover letter",
                ],
            ),
            ObservedField(
                element_id="field_cover",
                label=noisy_label,
                field_type="file",
                control_kind="file_upload",
                required=False,
            ),
        ],
    )

    resume_decision = validate_external_apply_action(
        observation=observation,
        proposed_action=ProposedAction(
            action_type="upload_file",
            element_id="field_resume",
            value=str(resume_path),
            confidence=0.96,
            risk="low",
            reason="Upload the configured resume.",
            source="profile",
        ),
        profile_facts={"resume_path": str(resume_path), "cover_letter_path": str(cover_letter_path)},
    )
    cover_decision = validate_external_apply_action(
        observation=observation,
        proposed_action=ProposedAction(
            action_type="upload_file",
            element_id="field_cover",
            value=str(cover_letter_path),
            confidence=0.74,
            risk="low",
            reason="Upload the generated cover letter.",
            source="profile",
        ),
        profile_facts={"resume_path": str(resume_path), "cover_letter_path": str(cover_letter_path)},
    )

    assert resume_decision.decision == "allowed"
    assert cover_decision.decision == "allowed"


def test_policy_pauses_profile_resume_upload_to_non_resume_document_field(tmp_path) -> None:
    resume_path = tmp_path / "resume.docx"
    resume_path.write_bytes(b"docx")
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[ObservedField(element_id="field_cover", label="Cover letter", field_type="file", required=True)],
    )
    action = ProposedAction(
        action_type="upload_file",
        element_id="field_cover",
        value=str(resume_path),
        confidence=0.96,
        risk="low",
        reason="Planner chose the configured profile resume.",
        source="profile",
    )

    decision = validate_external_apply_action(
        observation=observation,
        proposed_action=action,
        profile_facts={"resume_path": str(resume_path)},
    )

    assert decision.decision == "paused"
    assert "profile_resume_target_mismatch" in decision.risk_flags


def test_policy_pauses_upload_when_file_is_missing() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[ObservedField(element_id="field_resume", label="Resume", field_type="file", required=True)],
    )
    action = ProposedAction(
        action_type="upload_file",
        element_id="field_resume",
        value="C:/workspace/profile/missing.docx",
        confidence=0.96,
        risk="low",
        reason="Planner chose the configured profile resume.",
        source="profile",
    )

    decision = validate_external_apply_action(
        observation=observation,
        proposed_action=action,
        profile_facts={"resume_path": "C:/workspace/profile/missing.docx"},
    )

    assert decision.decision == "paused"
    assert "upload_file_missing" in decision.risk_flags


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


def test_policy_allows_inferred_career_narrative_text_answer() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[
            ObservedField(
                element_id="field_interest",
                label="Briefly outline why you are interested in this opportunity.*",
                field_type="textarea",
                required=True,
            )
        ],
    )
    action = ProposedAction(
        action_type="fill_text",
        element_id="field_interest",
        value="I am interested because the role aligns with my data platform leadership experience.",
        confidence=0.88,
        risk="medium",
        reason="Synthesised from the candidate profile and observed job context.",
        source="inferred",
    )

    decision = validate_external_apply_action(observation=observation, proposed_action=action)

    assert decision.decision == "allowed"


def test_policy_allows_inferred_profile_grounded_experience_text_answer() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[
            ObservedField(
                element_id="field_databricks",
                label=(
                    "Please outline your experience using Databricks. How many years' experience do you have "
                    "and how have you used it?*"
                ),
                field_type="textarea",
                required=True,
            )
        ],
    )
    action = ProposedAction(
        action_type="fill_text",
        element_id="field_databricks",
        value=(
            "I have hands-on Databricks experience building modern data platforms, metadata driven ingestion "
            "frameworks, and Spark based transformation workflows."
        ),
        confidence=0.88,
        risk="medium",
        reason="Synthesised from Databricks evidence in the candidate profile without inventing an exact duration.",
        source="inferred",
    )

    decision = validate_external_apply_action(observation=observation, proposed_action=action)

    assert decision.decision == "allowed"


def test_policy_allows_inferred_leadership_experience_text_answer() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[
            ObservedField(
                element_id="field_leadership",
                label=(
                    "Please describe your leadership experience. How many people have reported to you "
                    "at any given time and what were their roles?*"
                ),
                field_type="textarea",
                required=True,
            )
        ],
    )
    action = ProposedAction(
        action_type="fill_text",
        element_id="field_leadership",
        value=(
            "I have led technical delivery and architecture work, including a 50 member engineering team on a "
            "digital transformation initiative."
        ),
        confidence=0.9,
        risk="medium",
        reason="Synthesised from leadership evidence in the candidate profile.",
        source="inferred",
    )

    decision = validate_external_apply_action(observation=observation, proposed_action=action)

    assert decision.decision == "allowed"
