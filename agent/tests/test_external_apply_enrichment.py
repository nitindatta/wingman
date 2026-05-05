from app.services.external_apply_enrichment import enrich_page_observation, observation_quality_issues
from app.state.external_apply import ObservedField, PageObservation


def test_enrichment_classifies_profile_contact_fields_and_quality() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        page_type="form",
        fields=[
            ObservedField(element_id="field_email", label="Email Address *", field_type="email", required=True),
            ObservedField(element_id="field_phone", label="Mobile Phone *", field_type="phone", required=True),
            ObservedField(element_id="field_unknown", label="", field_type="text", required=True),
        ],
    )

    enriched = enrich_page_observation(
        observation,
        {
            "contact": {"email": "nitin@example.com", "phone": "0400000000"},
        },
    )

    email, phone, unknown = enriched.fields
    assert email.label_quality == "good"
    assert email.profile_fact == "email"
    assert email.answerability == "profile"
    assert phone.profile_fact == "phone"
    assert phone.answerability == "profile"
    assert unknown.label_quality == "missing"
    assert unknown.answerability == "unsafe_unknown"
    assert observation_quality_issues(enriched) == [
        "field_unknown: required field has missing label",
        "field_unknown: required field cannot be safely classified",
    ]


def test_enrichment_keeps_sensitive_self_report_user_required() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        page_type="form",
        fields=[
            ObservedField(
                element_id="field_atsi",
                label="Do you identify as Aboriginal/Torres Strait Islander?",
                field_type="radio",
                required=True,
                options=["Yes", "No", "Prefer not to answer", "Not applicable"],
            ),
            ObservedField(
                element_id="field_gender",
                label="Gender",
                field_type="radio",
                required=True,
                options=["Male", "Female", "Non-binary", "Prefer not to say"],
            ),
        ],
    )

    enriched = enrich_page_observation(observation, {})

    assert enriched.fields[0].sensitivity == "personal_sensitive"
    assert enriched.fields[0].answerability == "user_required"
    assert enriched.fields[1].sensitivity == "personal_sensitive"
    assert enriched.fields[1].answerability == "user_required"


def test_enrichment_classifies_noisy_resume_and_ordered_cover_letter_uploads() -> None:
    noisy_label = (
        "var regexInvalidFilenameCharacters = '[?\\'\"\\:<>|]'; "
        "Senior Data Innovation Lead - Copy Posted: 01/05/2026 Closing Date: 01/06/2026"
    )
    observation = PageObservation(
        url="https://ats.example/apply#/step1",
        page_type="resume_upload",
        visible_text="Your current resume must be uploaded in order to submit this application.",
        fields=[
            ObservedField(element_id="field_resume", label=noisy_label, field_type="file", required=False),
            ObservedField(
                element_id="field_cover_choice",
                label="No cover letter",
                field_type="radio",
                options=["No cover letter", "Upload my cover letter from my computer"],
                current_value="Upload my cover letter from my computer",
            ),
            ObservedField(element_id="field_cover", label=noisy_label, field_type="file", required=False),
            ObservedField(
                element_id="field_other",
                label="Please attach any other relevant documentation (optional)",
                field_type="file",
                required=False,
            ),
        ],
    )

    enriched = enrich_page_observation(
        observation,
        {
            "resume_path": "C:/workspace/profile/resume.docx",
            "cover_letter_path": "C:/workspace/cover.txt",
        },
    )

    assert enriched.fields[0].label_quality == "weak"
    assert enriched.fields[0].document_kind == "resume"
    assert enriched.fields[0].answerability == "profile"
    assert enriched.fields[2].document_kind == "cover_letter"
    assert enriched.fields[2].profile_fact == "cover_letter_path"
    assert enriched.fields[2].answerability == "profile"
    assert enriched.fields[3].document_kind == "additional_document"
    assert enriched.fields[3].answerability == "optional_skip"


def test_enrichment_marks_career_narrative_as_inferable() -> None:
    observation = PageObservation(
        url="https://ats.example/apply",
        fields=[
            ObservedField(
                element_id="field_databricks",
                label="Please outline your experience using Databricks and how you have used it.",
                field_type="textarea",
                required=True,
            )
        ],
    )

    enriched = enrich_page_observation(observation, {"evidence_items": [{"skills": ["Databricks"]}]})

    assert enriched.fields[0].answerability == "inferable"
    assert enriched.fields[0].sensitivity == "none"
