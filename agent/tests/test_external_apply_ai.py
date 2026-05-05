import json

import pytest

from app.services.external_apply_ai import (
    _append_external_apply_llm_transcript,
    _observation_for_prompt,
    build_external_apply_batch_planner_messages,
    build_external_apply_planner_messages,
    fallback_proposed_action,
    fallback_proposed_actions,
    parse_planner_batch_response,
    parse_planner_response,
)
from app.state.external_apply import ActionResult, ActionTrace, ObservedAction, ObservedField, PageObservation, ProposedAction


def test_parse_planner_response_accepts_known_observed_element() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[ObservedField(element_id="field_1", label="Full name", field_type="text")],
    )

    action = parse_planner_response(
        '{"action_type":"fill_text","element_id":"field_1","value":"Nitin Datta",'
        '"confidence":0.93,"risk":"low","reason":"Full name field.","source":"profile"}',
        observation,
    )

    assert action.action_type == "fill_text"
    assert action.element_id == "field_1"
    assert action.value == "Nitin Datta"


def test_parse_planner_batch_response_accepts_multiple_known_fields() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[
            ObservedField(element_id="field_1", label="First name", field_type="text"),
            ObservedField(element_id="field_2", label="Last name", field_type="text"),
        ],
    )

    actions = parse_planner_batch_response(
        '{"actions":['
        '{"action_type":"fill_text","element_id":"field_1","value":"Nitin",'
        '"confidence":0.96,"risk":"low","reason":"First name field.","source":"profile"},'
        '{"action_type":"fill_text","element_id":"field_2","value":"Datta",'
        '"confidence":0.96,"risk":"low","reason":"Last name field.","source":"profile"}'
        "]}",
        observation,
    )

    assert [action.element_id for action in actions] == ["field_1", "field_2"]


def test_parse_planner_response_rejects_unknown_element_id() -> None:
    observation = PageObservation(url="https://ats.example/apply")

    with pytest.raises(ValueError):
        parse_planner_response(
            '{"action_type":"click","element_id":"button_missing","confidence":0.9,'
            '"risk":"low","reason":"Click it.","source":"page"}',
            observation,
        )


def test_fallback_fills_safe_profile_field() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[ObservedField(element_id="field_1", label="Email address", field_type="email")],
    )

    action = fallback_proposed_action(observation, {"email": "nitin@example.com"}, [])

    assert action.action_type == "fill_text"
    assert action.element_id == "field_1"
    assert action.value == "nitin@example.com"
    assert action.source == "profile"


def test_fallback_retries_invalid_field_even_when_it_has_a_value() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[
            ObservedField(
                element_id="field_postcode",
                label="Postcode",
                field_type="text",
                current_value="abc",
                invalid=True,
                validation_message="Enter a valid postcode.",
            )
        ],
    )

    action = fallback_proposed_action(
        observation,
        {"address": {"postcode": "2000"}},
        [],
    )

    assert action.action_type == "fill_text"
    assert action.element_id == "field_postcode"
    assert action.value == "2000"
    assert action.source == "profile"


def test_batch_fallback_retries_invalid_field_before_disabled_continue_pause() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[
            ObservedField(
                element_id="field_phone",
                label="Phone Number",
                field_type="phone",
                current_value="bad",
                invalid=True,
                validation_message="Enter a valid phone number.",
            )
        ],
        buttons=[ObservedAction(element_id="button_continue", label="Save and Continue", disabled=True)],
    )

    actions = fallback_proposed_actions(
        observation,
        {"phone": "0400000000"},
        [],
    )

    assert [action.action_type for action in actions] == ["fill_text"]
    assert actions[0].element_id == "field_phone"
    assert actions[0].value == "0400000000"


def test_prompt_keeps_navigation_buttons_when_many_edit_buttons_are_observed() -> None:
    observation = PageObservation(
        url="https://au.seek.com/job/91685860/apply/profile",
        title="Update SEEK Profile | SEEK",
        page_type="review",
        buttons=[
            ObservedAction(element_id=f"button_edit_{index}", label=f"Edit role {index}", kind="button")
            for index in range(25)
        ]
        + [ObservedAction(element_id="button_continue", label="Continue", kind="submit")],
    )

    prompt_page = _observation_for_prompt(observation)

    assert "button_continue" in [button["element_id"] for button in prompt_page["buttons"]]


def test_fallback_fills_email_from_external_accounts_default() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[ObservedField(element_id="field_1", label="Email address", field_type="email")],
    )

    action = fallback_proposed_action(
        observation,
        {"external_accounts": {"default": {"email": "nitin@example.com"}}},
        [],
    )

    assert action.action_type == "fill_text"
    assert action.element_id == "field_1"
    assert action.value == "nitin@example.com"
    assert action.source == "profile"


def test_fallback_fills_password_from_external_accounts_default() -> None:
    observation = PageObservation(
        url="https://ats.example/login",
        fields=[ObservedField(element_id="field_password", label="Password", field_type="text")],
    )

    action = fallback_proposed_action(
        observation,
        {"external_accounts": {"default": {"password": "Sunshine@123#5"}}},
        [],
    )

    assert action.action_type == "fill_text"
    assert action.element_id == "field_password"
    assert action.value == "Sunshine@123#5"
    assert action.source == "profile"


def test_fallback_answers_prior_employment_question_from_profile_employer_history() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[ObservedField(element_id="field_svha", label="Have you previously worked at St Vincent's Health Australia (SVHA)?", field_type="radio")],
    )

    action = fallback_proposed_action(
        observation,
        {"employment_history": {"employers": ["AWS", "Department for Education, South Australia"]}},
        [],
    )

    assert action.action_type == "set_radio"
    assert action.element_id == "field_svha"
    assert action.value == "No"
    assert action.source == "profile"


def test_fallback_fills_salutation_from_external_accounts_default() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[ObservedField(element_id="field_salutation", label="What salutation should be selected?", field_type="select")],
    )

    action = fallback_proposed_action(
        observation,
        {"external_accounts": {"default": {"salutation": "Mr"}}},
        [],
    )

    assert action.action_type == "select_option"
    assert action.element_id == "field_salutation"
    assert action.value == "Mr"
    assert action.source == "profile"


def test_fallback_fills_home_address_from_canonical_profile() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[ObservedField(element_id="field_address", label="Home address", field_type="text", required=True)],
    )

    action = fallback_proposed_action(
        observation,
        {"address": {"street": "123 Example Street", "suburb": "Sampleville", "postcode": "2000"}},
        [],
    )

    assert action.action_type == "fill_text"
    assert action.element_id == "field_address"
    assert action.value == "123 Example Street"
    assert action.source == "profile"


def test_fallback_uploads_resume_from_profile_facts() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[ObservedField(element_id="field_resume", label="Resume", field_type="file", required=True)],
    )

    action = fallback_proposed_action(
        observation,
        {"resume_path": "C:/workspace/profile/example_resume.docx"},
        [],
    )

    assert action.action_type == "upload_file"
    assert action.element_id == "field_resume"
    assert action.value == "C:/workspace/profile/example_resume.docx"
    assert action.source == "profile"


def test_batch_fallback_does_not_use_resume_for_non_resume_file_upload() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[ObservedField(element_id="field_cover", label="Cover letter", field_type="file", required=True)],
    )

    actions = fallback_proposed_actions(
        observation,
        {"resume_path": "C:/workspace/profile/example_resume.docx"},
        [],
    )

    assert actions[0].action_type == "ask_user"
    assert actions[0].element_id == "field_cover"


def test_fallback_uploads_generated_cover_letter_file_for_cover_letter_upload() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[ObservedField(element_id="field_cover", label="Cover letter", field_type="file", required=False)],
    )

    action = fallback_proposed_action(
        observation,
        {
            "resume_path": "C:/workspace/profile/example_resume.docx",
            "cover_letter_path": "C:/workspace/automation/cover_letters/app-1_cover_letter.txt",
        },
        [],
    )

    assert action.action_type == "upload_file"
    assert action.element_id == "field_cover"
    assert action.value == "C:/workspace/automation/cover_letters/app-1_cover_letter.txt"
    assert action.source == "profile"


def test_fallback_pastes_generated_cover_letter_for_cover_letter_textarea() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[ObservedField(element_id="field_cover", label="Cover letter", field_type="textarea", required=True)],
    )

    action = fallback_proposed_action(
        observation,
        {"cover_letter": "Dear Hiring Team,\n\nI am excited to apply.\n\nKind regards,\nNitin"},
        [],
    )

    assert action.action_type == "fill_text"
    assert action.element_id == "field_cover"
    assert action.value == "Dear Hiring Team,\n\nI am excited to apply.\n\nKind regards,\nNitin"
    assert action.source == "profile"


def test_fallback_prefers_manual_entry_on_profile_entry_choice_page() -> None:
    observation = PageObservation(
        url="https://secure.workforceready.com.au/apply",
        page_type="unknown",
        visible_text="What's the best way to get your info? Use my CV Type it in myself",
        buttons=[
            ObservedAction(element_id="button_cv", label="Use my CV", kind="button"),
            ObservedAction(element_id="button_manual", label="Type it in myself", kind="button"),
        ],
    )

    action = fallback_proposed_action(observation, {}, [])

    assert action.action_type == "click"
    assert action.element_id == "button_manual"


def test_fallback_asks_user_on_job_search_page() -> None:
    observation = PageObservation(
        url="https://hk.jobsdb.com/",
        title="Jobsdb",
        visible_text="Perform a job search What Suggestions will appear below the field as you type",
        fields=[ObservedField(element_id="field_1", label="What", field_type="text")],
    )

    action = fallback_proposed_action(observation, {"headline": "AI Engineer"}, [])

    assert action.action_type == "ask_user"
    assert "job-search page" in (action.question or "")


def test_fallback_asks_user_for_sensitive_field() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[ObservedField(element_id="field_salary", label="Expected salary", field_type="text")],
    )

    action = fallback_proposed_action(observation, {}, [])

    assert action.action_type == "ask_user"
    assert action.element_id == "field_salary"
    assert action.risk == "medium"


def test_fallback_stops_at_submit_button() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        buttons=[ObservedAction(element_id="button_submit", label="Submit application", kind="submit")],
    )

    action = fallback_proposed_action(observation, {}, [])

    assert action.action_type == "stop_ready_to_submit"
    assert action.element_id == "button_submit"
    assert action.risk == "high"


def test_fallback_clicks_apply_on_sparse_unknown_page() -> None:
    observation = PageObservation(
        url="https://ats.example/jobs/123",
        page_type="unknown",
        buttons=[
            ObservedAction(element_id="button_signin", label="Sign In", kind="submit"),
            ObservedAction(element_id="button_home", label="Home", kind="submit"),
            ObservedAction(element_id="button_apply", label="Apply", kind="button"),
            ObservedAction(element_id="button_readmore", label="Read More", kind="button"),
        ],
    )

    action = fallback_proposed_action(observation, {}, [])

    assert action.action_type == "click"
    assert action.element_id == "button_apply"
    assert action.source == "page"


def test_fallback_clicks_create_account_link_when_it_is_the_best_cta() -> None:
    observation = PageObservation(
        url="https://ats.example/jobs/123/apply",
        page_type="unknown",
        links=[
            ObservedAction(element_id="link_help", label="Forgot your password?", kind="link"),
            ObservedAction(element_id="link_create", label="Create Account", kind="link"),
        ],
    )

    action = fallback_proposed_action(observation, {}, [])

    assert action.action_type == "click"
    assert action.element_id == "link_create"
    assert action.source == "page"


def test_fallback_treats_apply_now_as_navigation_on_non_submit_page() -> None:
    observation = PageObservation(
        url="https://ats.example/jobs/123",
        page_type="unknown",
        buttons=[ObservedAction(element_id="button_apply_now", label="Apply Now", kind="button")],
    )

    action = fallback_proposed_action(observation, {}, [])

    assert action.action_type == "click"
    assert action.element_id == "button_apply_now"


def test_prompt_includes_allowed_actions_and_observation() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        title="Apply",
        fields=[ObservedField(element_id="field_1", label="Full name", field_type="text")],
    )

    system, user = build_external_apply_planner_messages(
        observation=observation,
        profile_facts={"name": "Nitin Datta"},
        approved_memory=[],
        recent_actions=[],
    )

    payload = json.loads(user)

    assert "propose exactly one next browser action" in system
    assert "fill_text" in payload["allowed_actions"]
    assert payload["page"]["fields"][0]["element_id"] == "field_1"
    assert payload["available_facts"]["name"] == "Nitin Datta"


def test_prompt_includes_deterministic_field_insights() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        title="Apply",
        fields=[
            ObservedField(element_id="field_email", label="Email Address", field_type="email", required=True),
            ObservedField(element_id="field_missing", label="", field_type="text", required=True),
        ],
    )

    system, user = build_external_apply_batch_planner_messages(
        observation=observation,
        profile_facts={"contact": {"email": "nitin@example.com"}},
        approved_memory=[],
        recent_actions=[],
    )
    payload = json.loads(user)

    assert "label_quality" in system
    assert payload["page"]["fields"][0]["profile_fact"] == "email"
    assert payload["page"]["fields"][0]["answerability"] == "profile"
    assert payload["page"]["fields"][1]["label_quality"] == "missing"
    assert payload["page"]["fields"][1]["answerability"] == "unsafe_unknown"
    assert payload["observation_quality_issues"] == [
        "field_missing: required field has missing label",
        "field_missing: required field cannot be safely classified",
    ]


def test_prompt_redacts_external_account_password() -> None:
    observation = PageObservation(
        url="https://ats.example/login",
        title="Login",
        fields=[ObservedField(element_id="field_password", label="Password", field_type="password")],
    )

    _, user = build_external_apply_batch_planner_messages(
        observation=observation,
        profile_facts={
            "external_accounts": {
                "default": {
                    "email": "nitin@example.com",
                    "password": "Sunshine@123#5",
                    "working_rights": "Permanent Resident",
                }
            }
        },
        approved_memory=[],
        recent_actions=[],
    )
    payload = json.loads(user)

    assert "Sunshine@123#5" not in user
    assert "password" not in payload["available_facts"]["external_accounts"]["default"]
    assert payload["available_facts"]["external_accounts"]["default"]["email"] == "nitin@example.com"
    assert payload["available_facts"]["external_accounts"]["default"]["working_rights"] == "Permanent Resident"


def test_prompt_redacts_password_values_from_recent_actions() -> None:
    observation = PageObservation(url="https://ats.example/login")
    trace = ActionTrace(
        observation=observation,
        proposed_action=ProposedAction(
            action_type="fill_text",
            element_id="field_password",
            value="Sunshine@123#5",
            confidence=0.99,
            risk="low",
            reason="Fill saved password.",
            source="profile",
        ),
        policy_decision="allowed",
        result=ActionResult(
            ok=True,
            action_type="fill_text",
            element_id="field_password",
            value_after="Sunshine@123#5",
        ),
    )

    _, user = build_external_apply_batch_planner_messages(
        observation=observation,
        profile_facts={},
        approved_memory=[],
        recent_actions=[trace],
    )
    payload = json.loads(user)
    recent = payload["recent_actions"][0]

    assert "Sunshine@123#5" not in user
    assert recent["action"]["value"] == "[REDACTED]"
    assert recent["result"]["value_after"] == "[REDACTED]"


def test_external_apply_llm_transcript_log_writes_full_redacted_record(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "external_apply_llm.jsonl"
    monkeypatch.setattr("app.services.external_apply_ai._TRANSCRIPT_LOG_PATH", log_path)
    observation = PageObservation(
        url="https://ats.example/login",
        title="Login",
        page_type="login",
        fields=[ObservedField(element_id="field_password", label="Password", field_type="password")],
    )

    _append_external_apply_llm_transcript(
        call="batch_plan",
        model="gpt-test",
        observation=observation,
        system="system prompt",
        user=json.dumps(
            {
                "available_facts": {
                    "external_accounts": {"default": {"email": "nitin@example.com", "password": "Sunshine@123#5"}}
                }
            }
        ),
        raw_response=json.dumps({"actions": [{"element_id": "field_password", "value": "Sunshine@123#5"}]}),
        parsed_response={"actions": [{"element_id": "field_password", "value": "Sunshine@123#5"}]},
    )

    record = json.loads(log_path.read_text(encoding="utf-8"))

    assert record["call"] == "batch_plan"
    assert record["request"]["system"] == "system prompt"
    assert "Sunshine@123#5" not in log_path.read_text(encoding="utf-8")
    assert "password" not in json.loads(record["request"]["user"])["available_facts"]["external_accounts"]["default"]
    assert json.loads(record["response"]["raw"])["actions"][0]["value"] == "[REDACTED]"
    assert record["response"]["parsed"]["actions"][0]["value"] == "[REDACTED]"


def test_prompt_includes_memory_context() -> None:
    observation = PageObservation(
        url="https://secure.workforceready.com.au/apply/login",
        page_type="login",
        buttons=[ObservedAction(element_id="button_create", label="Create a new account")],
    )

    system, user = build_external_apply_planner_messages(
        observation=observation,
        profile_facts={},
        approved_memory=[],
        recent_actions=[],
        memory_context={
            "portal_host": "secure.workforceready.com.au",
            "saved_login_rejected": True,
            "create_account_available": True,
            "recommendations": ["Prefer create-account."],
        },
        planning_frame={
            "phase": "account_recovery",
            "recommended_actions": [
                {
                    "action_type": "click",
                    "element_id": "button_create",
                    "reason": "Saved login was rejected and create account is visible.",
                }
            ],
            "blocked_actions": ["retry_rejected_default_password"],
        },
    )
    payload = json.loads(user)

    assert "planning_frame strategies" in system
    assert payload["memory_context"]["portal_host"] == "secure.workforceready.com.au"
    assert payload["memory_context"]["saved_login_rejected"] is True
    assert payload["planning_frame"]["phase"] == "account_recovery"
    assert payload["planning_frame"]["recommended_actions"][0]["element_id"] == "button_create"


def test_batch_prompt_asks_for_multiple_safe_field_actions() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        title="Apply",
        fields=[
            ObservedField(element_id="field_1", label="First name", field_type="text"),
            ObservedField(element_id="field_2", label="Last name", field_type="text"),
        ],
    )

    system, user = build_external_apply_batch_planner_messages(
        observation=observation,
        profile_facts={"first_name": "Nitin", "last_name": "Datta"},
        approved_memory=[],
        recent_actions=[],
    )

    payload = json.loads(user)

    assert "propose a page plan" in system
    assert "\"actions\"" in system
    assert "available_facts.cover_letter_path" in system
    assert payload["page"]["fields"][0]["element_id"] == "field_1"
    assert payload["available_facts"]["first_name"] == "Nitin"


def test_batch_prompt_allows_profile_grounded_career_narrative_answers() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        title="Apply",
        fields=[
            ObservedField(
                element_id="field_interest",
                label="Briefly outline why you are interested in this opportunity.*",
                field_type="textarea",
                required=True,
            )
        ],
        visible_text="Principal Data Engineer role focused on data platforms and analytics leadership.",
    )

    system, user = build_external_apply_batch_planner_messages(
        observation=observation,
        profile_facts={
            "name": "Nitin Datta",
            "headline": "Principal Data Engineer",
            "summary": "Senior data engineering leader focused on reliable analytics platforms.",
            "core_strengths": ["data platform architecture", "team leadership"],
            "evidence_items": [
                {
                    "role_title": "Principal Data Engineer",
                    "skills": ["Databricks", "AWS", "data modelling"],
                    "action": "Led delivery of governed data products for enterprise users.",
                    "outcome": "Improved reporting reliability and delivery confidence.",
                }
            ],
            "voice_profile": {"tone_labels": ["direct", "practical"]},
        },
        approved_memory=[],
        recent_actions=[],
    )

    payload = json.loads(user)

    assert "career narrative" in system
    assert "why you are interested" in system
    assert payload["available_facts"]["headline"] == "Principal Data Engineer"
    assert payload["available_facts"]["core_strengths"] == ["data platform architecture", "team leadership"]
    assert payload["available_facts"]["evidence_items"][0]["skills"] == ["Databricks", "AWS", "data modelling"]


def test_batch_prompt_allows_profile_grounded_experience_answers_without_exact_invention() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        title="Additional Questions",
        fields=[
            ObservedField(
                element_id="field_leadership",
                label=(
                    "Please describe your leadership experience. How many people have reported to you "
                    "at any given time and what were their roles?*"
                ),
                field_type="textarea",
                required=True,
            ),
            ObservedField(
                element_id="field_databricks",
                label=(
                    "Please outline your experience using Databricks. How many years' experience do you have "
                    "and how have you used it?*"
                ),
                field_type="textarea",
                required=True,
            ),
            ObservedField(
                element_id="field_ai",
                label="What is your experience with AI prototyping?*",
                field_type="textarea",
                required=True,
            ),
            ObservedField(
                element_id="field_power_bi",
                label="Do you use Power BI? if so, please outline how you use it and what for*",
                field_type="textarea",
                required=True,
            ),
        ],
        visible_text="Senior Data Engineer role with Databricks, AI prototyping, dashboards, and stakeholder work.",
    )

    system, user = build_external_apply_batch_planner_messages(
        observation=observation,
        profile_facts={
            "headline": "AI & Data Systems Engineer",
            "summary": "Hands on Data Engineer focused on scalable data and AI systems using Databricks and Spark.",
            "core_strengths": ["Databricks", "LLM pipelines", "agent-based architectures"],
            "evidence_items": [
                {
                    "role_title": "Senior Solution Architect",
                    "skills": ["technical leadership", "Azure"],
                    "action": "Led 50 member engineering team in digital transformation initiative.",
                },
                {
                    "role_title": "Data Engineer",
                    "skills": ["Databricks", "embeddings"],
                    "action": "Built a Databricks based modern data platform.",
                    "proof_points": [
                        "Developed a metadata driven ingestion and transformation framework.",
                        "Explored AI techniques including embeddings and probabilistic matching.",
                    ],
                },
            ],
        },
        approved_memory=[],
        recent_actions=[],
    )

    payload = json.loads(user)

    assert "profile-grounded experience answers" in system
    assert "leadership experience" in system
    assert "experience with named technologies" in system
    assert "instead of inventing exact years" in system
    assert "unevidenced yes/no claim" in system
    assert payload["page"]["fields"][1]["label"].startswith("Please outline your experience using Databricks")
    assert payload["available_facts"]["evidence_items"][0]["action"] == (
        "Led 50 member engineering team in digital transformation initiative."
    )
