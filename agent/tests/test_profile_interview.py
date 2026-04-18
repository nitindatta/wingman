from app.state.canonical_profile import CanonicalEvidenceItem, CanonicalProfile
from app.state.profile_interview import ProfileInterviewState
from app.workflows.profile_interview import run_profile_interview


class _FakeSettings:
    openai_base_url = "http://example.test/v1"
    openai_api_key = "test"
    openai_model = "fake-model"


async def _planner(_settings, item, gap, asked_question_ids):
    del item, asked_question_ids
    return {
        "question": f"Question for {gap}?",
        "suggested_answer": f"Suggested answer for {gap}.",
        "source_basis": [f"Basis for {gap}"],
        "improvement_hint": f"Hint for {gap}.",
    }


async def _interpreter(_settings, item, gap, question, answer):
    del item, question
    if gap == "metrics":
        return {"field_updates": {"metrics": [answer]}, "approximate": False, "notes": ""}
    return {"field_updates": {gap: answer, "metrics": []}, "approximate": False, "notes": ""}


async def _assessor(_settings, item, gap, question, answer):
    del item, gap, question, answer
    return {
        "score": 0.8,
        "dimension_scores": {
            "specificity": 0.8,
            "ownership": 0.7,
            "outcome_strength": 0.75,
            "metric_usefulness": 0.9,
            "groundedness": 0.85,
        },
        "strengths": ["clear context", "grounded wording"],
        "weaknesses": ["could still add a metric"],
        "next_focus": "Add a measurable impact if you can.",
        "confidence": "medium",
    }


def _build_state() -> ProfileInterviewState:
    profile = CanonicalProfile(
        name="Nitin Datta",
        evidence_items=[
            CanonicalEvidenceItem(
                id="dfe_entity_resolution",
                source="Department for Education",
                role_title="Data Engineer",
                action="Built a metadata-driven ingestion framework.",
                proof_points=["Built a metadata-driven ingestion framework."],
            ),
            CanonicalEvidenceItem(
                id="metabricks",
                source="Metabricks",
                role_title="Project",
                action="Built a metadata-driven data platform.",
                proof_points=["Built a metadata-driven data platform."],
            ),
        ],
    )
    return ProfileInterviewState(
        session_id="session-1",
        source_profile_path="profile/raw_profile.json",
        target_profile_path="profile/nitin_datta_profile.canonical.json",
        canonical_profile=profile,
    )


async def test_profile_interview_start_selects_first_gap_question() -> None:
    state = await run_profile_interview(
        _FakeSettings(),
        _build_state(),
        question_planner=_planner,
        answer_interpreter=_interpreter,
        answer_assessor=_assessor,
    )

    assert state.status == "waiting_for_user"
    assert state.current_item_id == "dfe_entity_resolution"
    assert state.current_gap == "situation"
    assert state.current_question == "Question for situation?"
    assert state.current_prompt.question == "Question for situation?"
    assert state.current_prompt.suggested_answer == "Suggested answer for situation."
    assert state.current_prompt.source_basis == ["Basis for situation"]
    assert state.current_prompt.improvement_hint == "Hint for situation."


async def test_profile_interview_answer_advances_to_next_gap() -> None:
    state = await run_profile_interview(
        _FakeSettings(),
        _build_state(),
        question_planner=_planner,
        answer_interpreter=_interpreter,
    )
    state.action = "answer"
    state.user_answer = "Needed a scalable way to master student records."

    updated = await run_profile_interview(
        _FakeSettings(),
        state,
        question_planner=_planner,
        answer_interpreter=_interpreter,
        answer_assessor=_assessor,
    )

    assert updated.draft_item is not None
    assert updated.draft_item.situation == "Needed a scalable way to master student records."
    assert updated.draft_item.tone_sample == "Needed a scalable way to master student records."
    saved = next(item for item in updated.canonical_profile.evidence_items if item.id == "dfe_entity_resolution")
    assert saved.situation == "Needed a scalable way to master student records."
    assert saved.tone_sample == "Needed a scalable way to master student records."
    assert updated.canonical_profile.voice_samples == [
        "Needed a scalable way to master student records."
    ]
    assert "direct" in updated.canonical_profile.voice_profile.tone_labels
    assert updated.canonical_profile.voice_profile.confidence == "low"
    assert updated.last_answer_assessment.score == 0.8
    assert updated.item_quality_scores["dfe_entity_resolution"] == 0.8
    assert updated.overall_answer_quality_score == 0.8
    assert updated.overall_profile_score is not None
    assert updated.status == "waiting_for_user"
    assert updated.current_gap == "task"
    assert updated.current_question == "Question for task?"
    assert updated.current_prompt.suggested_answer == "Suggested answer for task."


async def test_profile_interview_approve_marks_item_and_moves_on() -> None:
    state = _build_state()
    state.draft_item = state.canonical_profile.evidence_items[0].model_copy(deep=True)
    state.current_item_id = state.draft_item.id
    state.draft_item.situation = "Needed a scalable way to master student records."
    state.draft_item.task = "Own the matching design."
    state.draft_item.outcome = "Improved matching reliability."
    state.draft_item.metrics = ["Reduced onboarding effort"]
    state.open_gaps = []
    state.status = "reviewing"
    state.action = "approve"

    updated = await run_profile_interview(
        _FakeSettings(),
        state,
        question_planner=_planner,
        answer_interpreter=_interpreter,
        answer_assessor=_assessor,
    )

    approved = next(item for item in updated.canonical_profile.evidence_items if item.id == "dfe_entity_resolution")
    assert approved.confidence == "approved"
    assert updated.current_item_id == "metabricks"
    assert updated.status == "waiting_for_user"
    assert updated.current_gap == "situation"
    assert updated.current_prompt.question == "Question for situation?"


async def test_profile_interview_complete_saves_partial_progress_and_finishes() -> None:
    state = _build_state()
    state.draft_item = state.canonical_profile.evidence_items[0].model_copy(deep=True)
    state.current_item_id = state.draft_item.id
    state.draft_item.situation = "Needed a scalable way to master student records."
    state.open_gaps = ["task", "outcome", "metrics"]
    state.status = "waiting_for_user"
    state.action = "complete"

    updated = await run_profile_interview(
        _FakeSettings(),
        state,
        question_planner=_planner,
        answer_interpreter=_interpreter,
        answer_assessor=_assessor,
    )

    saved = next(item for item in updated.canonical_profile.evidence_items if item.id == "dfe_entity_resolution")
    assert saved.situation == "Needed a scalable way to master student records."
    assert saved.confidence == "draft"
    assert updated.status == "completed"
    assert updated.current_item_id == ""
    assert updated.draft_item is None


async def test_profile_interview_defer_saves_partial_progress_and_moves_to_next_item() -> None:
    state = _build_state()
    state.draft_item = state.canonical_profile.evidence_items[0].model_copy(deep=True)
    state.current_item_id = state.draft_item.id
    state.draft_item.situation = "Needed a scalable way to master student records."
    state.open_gaps = ["task", "outcome", "metrics"]
    state.status = "waiting_for_user"
    state.action = "defer"

    updated = await run_profile_interview(
        _FakeSettings(),
        state,
        question_planner=_planner,
        answer_interpreter=_interpreter,
        answer_assessor=_assessor,
    )

    saved = next(item for item in updated.canonical_profile.evidence_items if item.id == "dfe_entity_resolution")
    assert saved.situation == "Needed a scalable way to master student records."
    assert saved.confidence == "draft"
    assert updated.current_item_id == "metabricks"
    assert updated.status == "waiting_for_user"
    assert updated.current_gap == "situation"
