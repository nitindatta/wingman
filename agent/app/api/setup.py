"""Setup routes — first-run wizard support."""

from __future__ import annotations

from fastapi import APIRouter, Request

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
        "chrome_profile_exists": browser_status.get("profile_exists", False),
        "chrome_has_cookies": browser_status.get("has_cookies", False),
        "chrome_profile_dir": browser_status.get("profile_dir", ""),
        "providers": ["seek"],
    }
