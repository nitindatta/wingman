import json
from pathlib import Path
import shutil
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from app.tools.client import ToolClient


@pytest.fixture()
async def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    temp_root = Path.cwd() / "test-output" / f"profile-interview-{uuid.uuid4().hex}"
    temp_root.mkdir(parents=True, exist_ok=True)
    profile_path = temp_root / "nitin_datta_profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "name": "Nitin Datta",
                "headline": "AI & Data Systems Engineer",
                "summary": "Hands-on data engineer building practical systems.",
                "core_strengths": ["Databricks", "entity resolution"],
                "writing_samples": [
                    "I tend to work best when I own the problem end-to-end."
                ],
                "experience": [
                    {
                        "title": "Data Engineer",
                        "company": "Department for Education",
                        "highlights": [
                            "Built a metadata-driven ingestion framework."
                        ],
                        "metrics": ["Reduced onboarding effort"],
                    }
                ],
                "selected_projects": [],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("INTERNAL_AUTH_SECRET", "test-secret")
    monkeypatch.setenv("SQLITE_PATH", ":memory:")
    monkeypatch.setenv("PROFILE_PATH", str(profile_path))
    monkeypatch.setenv("RAW_PROFILE_PATH", str(temp_root / "raw_profile.json"))
    import app.settings as settings_module

    settings_module._settings = None

    app_instance = create_app()
    settings: Settings = app_instance.state.settings
    tool_client = ToolClient(
        settings,
        client=httpx.AsyncClient(
            base_url=settings.tools_base_url,
            headers={"X-Internal-Auth": settings.internal_auth_secret},
        ),
    )
    app_instance.state._test_tool_client = tool_client

    with TestClient(app_instance) as tc:
        app_instance.state.tool_client = tool_client
        yield tc

    await tool_client.aclose()
    settings_module._settings = None
    shutil.rmtree(temp_root, ignore_errors=True)


async def test_answer_persists_updated_canonical_profile(client: TestClient) -> None:
    start = client.post("/api/profile-interview/start", json={})
    assert start.status_code == 200
    session = start.json()

    answer = client.post(
        f"/api/profile-interview/{session['session_id']}/answer",
        json={"answer": "I usually start by getting close to the operational pain point, then I keep the design practical enough to ship."},
    )
    assert answer.status_code == 200

    target_path = Path(answer.json()["target_profile_path"])
    saved = json.loads(target_path.read_text(encoding="utf-8"))

    assert saved["voice_samples"][-1] == (
        "I usually start by getting close to the operational pain point, then I keep the design practical enough to ship."
    )
    assert "practical" in saved["voice_profile"]["tone_labels"]
    assert saved["voice_profile"]["prefers_first_person"] is True


async def test_start_profile_interview_can_target_specific_item(client: TestClient) -> None:
    start = client.post(
        "/api/profile-interview/start",
        json={"item_id": "department-for-education-data-engineer"},
    )
    assert start.status_code == 200

    session = start.json()
    assert session["current_item_id"] == "department-for-education-data-engineer"


async def test_setup_target_prefers_active_interview_state_over_stale_file(client: TestClient) -> None:
    start = client.post("/api/profile-interview/start", json={})
    assert start.status_code == 200
    session = start.json()

    answer = client.post(
        f"/api/profile-interview/{session['session_id']}/answer",
        json={"answer": "The department needed a scalable way to master student records."},
    )
    assert answer.status_code == 200

    target_path = Path(answer.json()["target_profile_path"])
    stale = json.loads(target_path.read_text(encoding="utf-8"))
    stale["evidence_items"][0]["situation"] = ""
    target_path.write_text(json.dumps(stale, indent=2), encoding="utf-8")

    response = client.get("/api/setup/profile/target")
    assert response.status_code == 200

    payload = response.json()
    assert (
        payload["target_profile"]["evidence_items"][0]["situation"]
        == "The department needed a scalable way to master student records."
    )
