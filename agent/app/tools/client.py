import httpx

from app.settings import Settings
from app.state.envelope import ToolEnvelope


class ToolServiceError(Exception):
    """Raised when the tools/ service returns an unexpected transport failure."""


class ToolClient:
    """Thin httpx wrapper for calling the tools/ service.

    All calls send the shared-secret header and parse responses into a
    ToolEnvelope. The caller is responsible for inspecting envelope.status
    and routing accordingly. Transport errors (HTTP 5xx, connection refused)
    raise ToolServiceError. Tool-level errors (status="error", "drift",
    "needs_human") are returned in the envelope.
    """

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(
            base_url=settings.tools_base_url,
            headers={"X-Internal-Auth": settings.internal_auth_secret},
            timeout=httpx.Timeout(connect=5.0, read=120.0, write=30.0, pool=5.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def call(self, path: str, payload: dict[str, object] | None = None) -> ToolEnvelope:
        try:
            response = await self._client.post(path, json=payload or {})
        except httpx.RequestError as exc:
            raise ToolServiceError(f"tools/ service unreachable: {exc}") from exc
        if response.status_code >= 500:
            raise ToolServiceError(
                f"tools/ service returned HTTP {response.status_code}: {response.text}"
            )
        return ToolEnvelope.model_validate(response.json())

    async def call_get(self, path: str) -> ToolEnvelope:
        try:
            response = await self._client.get(path)
        except httpx.RequestError as exc:
            raise ToolServiceError(f"tools/ service unreachable: {exc}") from exc
        if response.status_code >= 500:
            raise ToolServiceError(
                f"tools/ service returned HTTP {response.status_code}: {response.text}"
            )
        return ToolEnvelope.model_validate(response.json())

    async def health(self) -> ToolEnvelope:
        try:
            response = await self._client.get("/health")
        except httpx.RequestError as exc:
            raise ToolServiceError(f"tools/ service unreachable: {exc}") from exc
        return ToolEnvelope.model_validate(response.json())
