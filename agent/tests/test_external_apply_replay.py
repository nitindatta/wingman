import json
from pathlib import Path
from typing import Any

from app.services.external_apply_enrichment import enrich_page_observation, observation_quality_issues
from app.services.external_apply_harness import run_external_apply_step
from app.services.external_apply_policy import validate_external_apply_action
from app.state.external_apply import ActionResult, PageObservation, PolicyDecision, ProposedAction


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "external_apply"


class DummyToolClient:
    pass


class TempExternalAccountSettings:
    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def resolved_external_accounts_path(self) -> Path:
        return self._path


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _observation(fixture: dict[str, Any]) -> PageObservation:
    return PageObservation.model_validate(fixture["observation"])


def _fields_by_id(observation: PageObservation) -> dict[str, Any]:
    return {field.element_id: field for field in observation.fields}


def test_replay_fixtures_validate_against_observation_schema() -> None:
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        assert fixture["name"]
        assert PageObservation.model_validate(fixture["observation"]).url


def test_replay_fixtures_match_expected_enrichment() -> None:
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        profile_facts = fixture.get("profile_facts", {})
        if fixture["name"] == "elmo_noisy_upload":
            profile_facts = {
                "resume_path": "C:/workspace/profile/resume.docx",
                "cover_letter_path": "C:/workspace/automation/cover.txt",
            }
        enriched = enrich_page_observation(_observation(fixture), profile_facts)
        fields = _fields_by_id(enriched)

        for element_id, expected in fixture.get("expected_fields", {}).items():
            field = fields[element_id]
            for key, value in expected.items():
                assert getattr(field, key) == value, f"{fixture['name']} {element_id}.{key}"

        if "expected_quality_issues" in fixture:
            assert observation_quality_issues(enriched) == fixture["expected_quality_issues"]


def test_replay_noisy_upload_policy_uses_enriched_document_kind(tmp_path: Path) -> None:
    fixture = _fixture("elmo_noisy_upload")
    resume_path = tmp_path / "resume.docx"
    cover_path = tmp_path / "cover-letter.txt"
    resume_path.write_bytes(b"docx")
    cover_path.write_text("Dear Hiring Team", encoding="utf-8")
    enriched = enrich_page_observation(
        _observation(fixture),
        {"resume_path": str(resume_path), "cover_letter_path": str(cover_path)},
    )

    resume_decision = validate_external_apply_action(
        observation=enriched,
        proposed_action=ProposedAction(
            action_type="upload_file",
            element_id="field_resume",
            value=str(resume_path),
            confidence=0.76,
            risk="low",
            reason="Replay: upload resume.",
            source="profile",
        ),
        profile_facts={"resume_path": str(resume_path), "cover_letter_path": str(cover_path)},
    )
    cover_decision = validate_external_apply_action(
        observation=enriched,
        proposed_action=ProposedAction(
            action_type="upload_file",
            element_id="field_cover",
            value=str(cover_path),
            confidence=0.74,
            risk="low",
            reason="Replay: upload generated cover letter.",
            source="profile",
        ),
        profile_facts={"resume_path": str(resume_path), "cover_letter_path": str(cover_path)},
    )

    assert resume_decision.decision == "allowed"
    assert cover_decision.decision == "allowed"


async def test_replay_bad_labels_quality_gate_pauses_before_planner() -> None:
    fixture = _fixture("elmo_profile_form_bad_labels")

    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return _observation(fixture)

    async def planner(_settings: Any, **_kwargs: Any) -> ProposedAction:
        raise AssertionError("unsafe weak-label fields should pause before planner")

    state = await run_external_apply_step(
        TempExternalAccountSettings(Path("unused.json")),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts={},
        observe_fn=observe,
        planner_fn=planner,
    )

    assert state.status == "paused_for_user"
    assert [question.target_element_id for question in state.pending_user_questions] == [
        "field_11",
        "field_18",
        "field_19",
    ]
    assert state.pending_user_question is not None
    assert "could not identify a reliable label" in state.pending_user_question.question
    assert "this field" not in state.pending_user_question.question.lower()


async def test_replay_create_profile_fills_generated_portal_account_fields(tmp_path: Path) -> None:
    fixture = _fixture("portal_create_profile")

    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return _observation(fixture)

    async def planner(_settings: Any, **_kwargs: Any) -> ProposedAction:
        raise AssertionError("create-profile replay should be deterministic")

    def policy(**_kwargs: Any) -> PolicyDecision:
        return PolicyDecision(decision="allowed", reason="replay policy allow")

    executed: list[tuple[str, str | None]] = []

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        executed.append((action.action_type, action.element_id))
        return ActionResult(
            ok=True,
            action_type=action.action_type,
            element_id=action.element_id,
            value_after=action.value,
            new_url=fixture["observation"]["url"],
        )

    state = await run_external_apply_step(
        TempExternalAccountSettings(tmp_path / "external_accounts.json"),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts=fixture["profile_facts"],
        observe_fn=observe,
        planner_fn=planner,
        policy_fn=policy,
        execute_fn=execute,
    )

    assert executed == [tuple(item) for item in fixture["expected_actions"]]
    assert state.status == "running"
    saved = json.loads((tmp_path / "external_accounts.json").read_text(encoding="utf-8"))
    assert saved["portals"]["inghams.elmotalent.com.au"]["status"] == "pending_creation"


async def test_replay_existing_account_error_pauses_and_marks_portal(tmp_path: Path) -> None:
    fixture = _fixture("portal_email_already_registered")

    async def observe(_client: Any, _session_key: str) -> PageObservation:
        return _observation(fixture)

    async def planner(_settings: Any, **_kwargs: Any) -> ProposedAction:
        raise AssertionError("account-exists replay should stop before planner")

    async def execute(_client: Any, _session_key: str, action: ProposedAction) -> ActionResult:
        raise AssertionError(f"account-exists replay should not execute {action.action_type}")

    state = await run_external_apply_step(
        TempExternalAccountSettings(tmp_path / "external_accounts.json"),  # type: ignore[arg-type]
        DummyToolClient(),  # type: ignore[arg-type]
        session_key="session-1",
        application_id="app-1",
        profile_facts=fixture["profile_facts"],
        observe_fn=observe,
        planner_fn=planner,
        execute_fn=execute,
    )

    assert state.status == "paused_for_user"
    assert state.memory_context.account_status == "existing_account_detected"
    assert state.memory_context.credential_status == "needs_user"
    assert state.memory_context.credential_available is False
    assert state.pending_user_question is not None
    assert "already exists" in state.pending_user_question.question
    assert "sign in, reset the password" in state.pending_user_question.context
    assert state.risk_flags == ["existing_account_detected", "portal_credential_required"]

    saved = json.loads((tmp_path / "external_accounts.json").read_text(encoding="utf-8"))
    portal = saved["portals"]["inghams.elmotalent.com.au"]
    assert portal["status"] == "existing_account_detected"
    assert portal["credential_status"] == "needs_user"
    assert portal["account_mode"] == "login"
