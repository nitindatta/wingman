import json
from pathlib import Path
import shutil
import uuid

import pytest
from app.services.profile_store import (
    apply_canonical_profile_to_interview_state,
    load_or_build_target_profile,
)
from app.settings import Settings
from app.state.canonical_profile import CanonicalEvidenceItem, CanonicalProfile
from app.state.profile_interview import ProfileInterviewState


def _make_settings(repo_root: Path) -> Settings:
    return Settings(
        _env_file=None,
        internal_auth_secret="test-secret",
        repo_root=repo_root,
        sqlite_path=Path("automation/agent.db"),
        profile_path=Path("profile/profile.json"),
        resume_path=Path("profile/resume.docx"),
        raw_profile_path=Path("profile/raw_profile.json"),
        profile_answers_path=Path("profile/profile_answers.json"),
        profile_upload_dir=Path("automation/profile_uploads"),
    )


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _build_profile(situation: str) -> CanonicalProfile:
    return CanonicalProfile(
        name="Nitin Datta",
        evidence_items=[
            CanonicalEvidenceItem(
                id="department-for-education-south-australia-data-engineer",
                source="Department for Education, South Australia",
                role_title="Data Engineer",
                action="Built a Databricks based modern data platform.",
                situation=situation,
            )
        ],
    )


@pytest.fixture()
def repo_root() -> Path:
    root = Path.cwd() / "test-output" / f"profile-store-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_settings_auto_discovers_existing_profile_json_when_default_missing(repo_root: Path) -> None:
    profile_dir = repo_root / "profile"
    _write_json(profile_dir / "nitin_datta_profile.json", {"name": "Nitin Datta"})

    settings = _make_settings(repo_root)

    assert settings.resolved_profile_path == profile_dir / "nitin_datta_profile.json"
    assert settings.resolved_target_profile_path == profile_dir / "nitin_datta_profile.canonical.json"


def test_settings_prefers_named_canonical_profile_when_default_profile_is_missing(repo_root: Path) -> None:
    profile_dir = repo_root / "profile"
    _write_json(profile_dir / "profile.canonical.json", {"name": "Fallback"})
    _write_json(
        profile_dir / "nitin_datta_profile.canonical.json",
        {
            "name": "Nitin Datta",
            "address": {
                "street": "12 Swan Circuit",
                "postcode": "5095",
            },
        },
    )

    settings = _make_settings(repo_root)

    assert settings.resolved_profile_path == profile_dir / "profile.json"
    assert settings.resolved_target_profile_path == profile_dir / "nitin_datta_profile.canonical.json"


def test_settings_ignores_external_accounts_file_when_discovering_profile(repo_root: Path) -> None:
    profile_dir = repo_root / "profile"
    _write_json(
        profile_dir / "external_accounts.json",
        {"default": {"email": "nitin@example.com", "password": "secret"}},
    )
    _write_json(
        profile_dir / "nitin_datta_profile.canonical.json",
        {"name": "Nitin Datta"},
    )

    settings = _make_settings(repo_root)

    assert settings.resolved_profile_path == profile_dir / "profile.json"
    assert settings.resolved_external_accounts_path == profile_dir / "external_accounts.json"
    assert settings.resolved_target_profile_path == profile_dir / "nitin_datta_profile.canonical.json"


def test_settings_auto_discovers_first_docx_resume_when_default_missing(repo_root: Path) -> None:
    profile_dir = repo_root / "profile"
    profile_dir.mkdir(parents=True)
    (profile_dir / "b_resume.docx").write_bytes(b"docx-b")
    (profile_dir / "a_resume.docx").write_bytes(b"docx-a")

    settings = _make_settings(repo_root)

    assert settings.resolved_resume_path == profile_dir / "a_resume.docx"


def test_refresh_profile_interview_state_uses_discovered_target_profile(repo_root: Path) -> None:
    profile_dir = repo_root / "profile"
    _write_json(profile_dir / "nitin_datta_profile.json", {"name": "Nitin Datta"})
    target_profile = _build_profile("Saved situation from canonical profile")
    _write_json(
        profile_dir / "nitin_datta_profile.canonical.json",
        target_profile.model_dump(),
    )
    settings = _make_settings(repo_root)

    stale_profile = _build_profile("")
    state = ProfileInterviewState(
        session_id="session-1",
        source_profile_path=str(profile_dir / "profile.json"),
        target_profile_path=str(profile_dir / "profile.canonical.json"),
        canonical_profile=stale_profile,
        status="waiting_for_user",
        current_item_id="department-for-education-south-australia-data-engineer",
        selected_item_id="department-for-education-south-australia-data-engineer",
        draft_item=stale_profile.evidence_items[0].model_copy(deep=True),
    )

    changed = apply_canonical_profile_to_interview_state(
        state,
        canonical_profile=target_profile,
        source_profile_path=str(profile_dir / "nitin_datta_profile.json"),
        target_profile_path=str(profile_dir / "nitin_datta_profile.canonical.json"),
    )

    assert changed is True
    assert state.source_profile_path == str(profile_dir / "nitin_datta_profile.json")
    assert state.target_profile_path == str(profile_dir / "nitin_datta_profile.canonical.json")
    assert state.canonical_profile.evidence_items[0].situation == "Saved situation from canonical profile"
    assert state.draft_item is not None
    assert state.draft_item.situation == "Saved situation from canonical profile"


def test_refresh_profile_interview_state_preserves_pending_reflection(repo_root: Path) -> None:
    profile_dir = repo_root / "profile"
    _write_json(profile_dir / "nitin_datta_profile.json", {"name": "Nitin Datta"})
    target_profile = _build_profile("Saved situation from canonical profile")
    _write_json(
        profile_dir / "nitin_datta_profile.canonical.json",
        target_profile.model_dump(),
    )
    settings = _make_settings(repo_root)

    stale_profile = _build_profile("")
    pending_item = stale_profile.evidence_items[0].model_copy(deep=True)
    pending_item.situation = "Pending refined situation"
    state = ProfileInterviewState(
        session_id="session-2",
        source_profile_path=str(profile_dir / "profile.json"),
        target_profile_path=str(profile_dir / "profile.canonical.json"),
        canonical_profile=stale_profile,
        status="awaiting_confirmation",
        current_item_id="department-for-education-south-australia-data-engineer",
        selected_item_id="department-for-education-south-australia-data-engineer",
        draft_item=pending_item.model_copy(deep=True),
        pending_item=pending_item.model_copy(deep=True),
    )

    changed = apply_canonical_profile_to_interview_state(
        state,
        canonical_profile=target_profile,
        source_profile_path=str(profile_dir / "nitin_datta_profile.json"),
        target_profile_path=str(profile_dir / "nitin_datta_profile.canonical.json"),
    )

    assert changed is True
    assert state.canonical_profile.evidence_items[0].situation == "Saved situation from canonical profile"
    assert state.pending_item is not None
    assert state.pending_item.situation == "Pending refined situation"
    assert state.draft_item is not None
    assert state.draft_item.situation == "Pending refined situation"


def test_load_or_build_target_profile_uses_discovered_target_path(repo_root: Path) -> None:
    profile_dir = repo_root / "profile"
    _write_json(profile_dir / "nitin_datta_profile.json", {"name": "Nitin Datta"})
    target_profile = _build_profile("Saved situation from canonical profile")
    _write_json(
        profile_dir / "nitin_datta_profile.canonical.json",
        target_profile.model_dump(),
    )
    settings = _make_settings(repo_root)

    loaded = load_or_build_target_profile(settings)

    assert loaded is not None
    assert loaded.evidence_items[0].situation == "Saved situation from canonical profile"
