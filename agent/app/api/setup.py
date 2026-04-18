"""Setup routes — first-run wizard support."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.services.profile_ingest import (
    ProfileIngestError,
    build_raw_profile_from_legacy_json,
    extract_profile_from_saved_file,
    load_raw_profile,
    persist_uploaded_file,
    save_raw_profile,
)
from app.services.profile_target import (
    apply_profile_answers,
    build_canonical_profile,
    build_canonical_profile_from_raw_profile,
    build_profile_enrichment_questions,
)
from app.settings import Settings
from app.state.canonical_profile import CanonicalProfile, ProfileAnswer, ProfileTargetResponse
from app.state.raw_profile import ProfileUploadResponse, RawProfileResponse, SourceDocument

router = APIRouter()


@router.post("/api/setup/login/{provider}", response_model=dict)
async def open_provider_login(provider: str, request: Request):
    """Tell the tools service to open Chrome at the provider login page."""
    tool_client = request.app.state.tool_client
    env = await tool_client.call("/tools/browser/open_for_login", {"provider": provider})
    if env.status == "error":
        return {"ok": False, "error": env.error.message if env.error else "unknown error"}
    return {"ok": True, **(env.data or {})}


@router.get("/api/setup/status", response_model=dict)
async def setup_status(request: Request):
    """Return first-run checklist state."""
    settings = request.app.state.settings

    profile_json_exists = settings.resolved_profile_path.exists()
    raw_profile_exists = settings.resolved_raw_profile_path.exists()
    target_profile_exists = settings.resolved_target_profile_path.exists()
    latest_upload = _latest_uploaded_file(settings.resolved_profile_upload_dir)

    # Ask tools service for browser profile state
    tool_client = request.app.state.tool_client
    browser_status: dict = {}
    try:
        env = await tool_client.call_get("/tools/setup/status")
        if env.status == "ok":
            browser_status = env.data or {}
    except Exception:
        pass

    return {
        "profile_json_exists": profile_json_exists,
        "profile_json_path": str(settings.resolved_profile_path),
        "raw_profile_exists": raw_profile_exists,
        "raw_profile_path": str(settings.resolved_raw_profile_path),
        "target_profile_exists": target_profile_exists,
        "target_profile_path": str(settings.resolved_target_profile_path),
        "latest_uploaded_filename": latest_upload.name if latest_upload else "",
        "chrome_profile_exists": browser_status.get("profile_exists", False),
        "chrome_has_cookies": browser_status.get("has_cookies", False),
        "chrome_profile_dir": browser_status.get("profile_dir", ""),
        "providers": ["seek"],
    }


@router.post("/api/setup/profile/upload", response_model=ProfileUploadResponse)
async def upload_source_profile(request: Request) -> ProfileUploadResponse:
    """Accept a resume/profile upload, store it locally, extract it, and persist raw_profile.json."""
    settings = request.app.state.settings
    try:
        form = await request.form()
    except AssertionError as exc:
        raise HTTPException(
            status_code=500,
            detail="python-multipart is required for uploads. Run `uv sync` in the agent directory.",
        ) from exc

    upload = form.get("file")
    if upload is None or not hasattr(upload, "filename"):
        raise HTTPException(status_code=400, detail="missing uploaded file")

    filename = str(getattr(upload, "filename", "") or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="uploaded file has no filename")

    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".docx", ".json"}:
        raise HTTPException(status_code=400, detail="supported file types: .pdf, .docx, .json")

    content = await upload.read()
    if not content:
        raise HTTPException(status_code=400, detail="uploaded file is empty")

    source_document, _upload_dir = persist_uploaded_file(
        settings,
        filename=filename,
        content_type=str(getattr(upload, "content_type", "") or ""),
        content=content,
    )

    try:
        extracted = extract_profile_from_saved_file(settings, source_document)
    except (ProfileIngestError, json.JSONDecodeError) as exc:
        source_document.parse_status = "failed"
        source_document.parse_error = str(exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    raw_profile_path = save_raw_profile(settings, extracted.raw_profile)
    return ProfileUploadResponse(
        ok=True,
        source_document=extracted.source_document,
        raw_profile_path=str(raw_profile_path),
        raw_profile=extracted.raw_profile,
    )


@router.get("/api/setup/profile/raw", response_model=RawProfileResponse)
async def get_raw_profile(request: Request) -> RawProfileResponse:
    """Return the current raw parsed profile, if one exists."""
    settings = request.app.state.settings
    raw_profile = load_raw_profile(settings)
    if raw_profile is not None:
        return RawProfileResponse(
            raw_profile_exists=True,
            raw_profile_path=str(settings.resolved_raw_profile_path),
            raw_profile=raw_profile,
        )

    if settings.resolved_profile_path.exists():
        legacy_data = json.loads(settings.resolved_profile_path.read_text(encoding="utf-8"))
        source_document = _legacy_source_document(settings.resolved_profile_path)
        derived = build_raw_profile_from_legacy_json(legacy_data, source_document)
        return RawProfileResponse(
            raw_profile_exists=False,
            raw_profile_path=str(settings.resolved_raw_profile_path),
            raw_profile=derived,
        )

    return RawProfileResponse(
        raw_profile_exists=False,
        raw_profile_path=str(settings.resolved_raw_profile_path),
    )


@router.get("/api/setup/profile/target", response_model=ProfileTargetResponse)
async def get_target_profile(request: Request) -> ProfileTargetResponse:
    """Return the canonical target profile draft plus focused enrichment questions."""
    settings = request.app.state.settings
    target_path = settings.resolved_target_profile_path
    target_profile, profile_exists, source_profile_path = await _load_target_profile_for_setup(request)
    if not profile_exists or target_profile is None:
        return ProfileTargetResponse(
            profile_exists=False,
            source_profile_path=str(settings.resolved_raw_profile_path),
            target_profile_path=str(target_path),
            target_profile_exists=target_path.exists(),
        )

    return ProfileTargetResponse(
        profile_exists=True,
        source_profile_path=source_profile_path,
        target_profile_path=str(target_path),
        target_profile_exists=target_path.exists(),
        target_profile=target_profile,
        questions=build_profile_enrichment_questions(target_profile),
    )


class SaveTargetProfileRequest(BaseModel):
    target_profile: CanonicalProfile


class SaveProfileAnswersRequest(BaseModel):
    answers: list[ProfileAnswer]


@router.post("/api/setup/profile/target", response_model=dict)
async def save_target_profile(body: SaveTargetProfileRequest, request: Request) -> dict[str, object]:
    """Persist the canonical target profile beside the source profile JSON."""
    target_path = request.app.state.settings.resolved_target_profile_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        body.target_profile.model_dump_json(indent=2),
        encoding="utf-8",
    )
    await _sync_active_interview_profile(request, body.target_profile)
    return {"ok": True, "target_profile_path": str(target_path)}


@router.post("/api/setup/profile/target/answers", response_model=ProfileTargetResponse)
async def save_target_profile_answers(
    body: SaveProfileAnswersRequest,
    request: Request,
) -> ProfileTargetResponse:
    settings = request.app.state.settings
    target_profile, profile_exists = _load_or_build_target_profile(settings)
    if not profile_exists or target_profile is None:
        raise HTTPException(status_code=404, detail="no source profile is available yet")

    updated_profile = apply_profile_answers(target_profile, body.answers)

    answers_path = settings.resolved_profile_answers_path
    answers_path.parent.mkdir(parents=True, exist_ok=True)
    answers_path.write_text(
        json.dumps([answer.model_dump() for answer in body.answers], indent=2),
        encoding="utf-8",
    )

    target_path = settings.resolved_target_profile_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(updated_profile.model_dump_json(indent=2), encoding="utf-8")
    await _sync_active_interview_profile(request, updated_profile)

    return ProfileTargetResponse(
        profile_exists=True,
        source_profile_path=str(
            settings.resolved_raw_profile_path
            if settings.resolved_raw_profile_path.exists()
            else settings.resolved_profile_path
        ),
        target_profile_path=str(target_path),
        target_profile_exists=True,
        target_profile=updated_profile,
        questions=build_profile_enrichment_questions(updated_profile),
    )


def _latest_uploaded_file(upload_root: Path) -> Path | None:
    if not upload_root.exists():
        return None
    candidates = [path for path in upload_root.rglob("*") if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _legacy_source_document(path: Path) -> SourceDocument:
    content = path.read_bytes()
    return SourceDocument(
        id="legacy-profile",
        filename=path.name,
        mime_type="application/json",
        saved_path=str(path),
        sha256=hashlib.sha256(content).hexdigest(),
        parse_status="success",
    )


def _load_or_build_target_profile(settings: Settings) -> tuple[CanonicalProfile | None, bool]:
    source_path = settings.resolved_profile_path
    target_path = settings.resolved_target_profile_path
    raw_profile = load_raw_profile(settings)

    if raw_profile is None and not source_path.exists():
        return None, False

    if target_path.exists():
        return (
            CanonicalProfile.model_validate_json(target_path.read_text(encoding="utf-8")),
            True,
        )

    if raw_profile is not None:
        return build_canonical_profile_from_raw_profile(raw_profile), True

    legacy_profile = json.loads(source_path.read_text(encoding="utf-8"))
    return build_canonical_profile(legacy_profile), True


async def _load_target_profile_for_setup(
    request: Request,
) -> tuple[CanonicalProfile | None, bool, str]:
    settings = request.app.state.settings
    repo = request.app.state.profile_interview_repository
    active = await repo.get_active()
    if active is not None:
        return (
            active.canonical_profile,
            True,
            active.source_profile_path,
        )

    target_profile, profile_exists = _load_or_build_target_profile(settings)
    source_profile_path = str(
        settings.resolved_raw_profile_path
        if settings.resolved_raw_profile_path.exists()
        else settings.resolved_profile_path
    )
    return target_profile, profile_exists, source_profile_path


async def _sync_active_interview_profile(request: Request, profile: CanonicalProfile) -> None:
    repo = request.app.state.profile_interview_repository
    active = await repo.get_active()
    if active is None:
        return

    updated = active.model_copy(deep=True)
    updated.canonical_profile = profile.model_copy(deep=True)
    active_item_id = updated.selected_item_id or updated.current_item_id
    if active_item_id:
        updated.draft_item = next(
            (
                item.model_copy(deep=True)
                for item in updated.canonical_profile.evidence_items
                if item.id == active_item_id
            ),
            None,
        )
    await repo.save_state(updated)
