from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the Python agent service.

    Values are read from environment variables with fallbacks documented
    in the field definitions. Secrets must come from the environment; defaults
    are only provided for development convenience.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # FastAPI
    api_host: str = Field(default="127.0.0.1")
    api_port: int = Field(default=8005)

    # tools/ service
    tools_base_url: str = Field(default="http://127.0.0.1:4320")
    internal_auth_secret: str = Field(...)

    # LLM (OpenAI-compatible local proxy)
    openai_base_url: str = Field(
        default="http://127.0.0.1:8123/v1",
        validation_alias="OPENAI_COMPAT_BASE_URL",
    )
    openai_api_key: str = Field(
        default="local-dev-key",
        validation_alias="OPENAI_COMPAT_API_KEY",
    )
    openai_model: str = Field(
        default="gpt-5.4",
        validation_alias="OPENAI_COMPAT_MODEL",
    )

    # Worker
    worker_prepare_concurrency: int = Field(default=2, validation_alias="WORKER_PREPARE_CONCURRENCY")

    # External apply harness
    external_apply_harness_enabled: bool = Field(
        default=True,
        validation_alias="EXTERNAL_APPLY_HARNESS_ENABLED",
    )

    # Persistence
    repo_root: Path = Field(default=Path(__file__).resolve().parents[2])
    sqlite_path: Path = Field(default=Path("../automation/agent.db"))
    profile_path: Path = Field(default=Path("profile/profile.json"))
    resume_path: Path = Field(default=Path("profile/resume.docx"))
    raw_profile_path: Path = Field(default=Path("profile/raw_profile.json"))
    profile_answers_path: Path = Field(default=Path("profile/profile_answers.json"))
    external_accounts_path: Path = Field(default=Path("profile/external_accounts.json"))
    profile_upload_dir: Path = Field(default=Path("automation/profile_uploads"))

    def _discover_profile_path(self, configured_path: Path) -> Path | None:
        directory = configured_path.parent
        if not directory.exists():
            return None

        candidates: list[Path] = []
        for candidate in directory.glob("*.json"):
            name = candidate.name.lower()
            if name in {"raw_profile.json", "profile_answers.json", "external_accounts.json"}:
                continue
            if name.endswith(".canonical.json"):
                continue
            candidates.append(candidate.resolve())

        if len(candidates) == 1:
            return candidates[0]

        preferred = [candidate for candidate in candidates if candidate.stem.endswith("_profile")]
        if len(preferred) == 1:
            return preferred[0]

        return None

    def _discover_target_profile_path(self, configured_path: Path) -> Path | None:
        directory = configured_path.parent
        if not directory.exists():
            return None

        candidates = sorted(directory.glob("*.canonical.json"), key=lambda candidate: candidate.name.lower())
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0].resolve()

        preferred = [candidate.resolve() for candidate in candidates if candidate.stem.endswith("_profile.canonical")]
        if len(preferred) == 1:
            return preferred[0]

        return None

    def _discover_resume_path(self, configured_path: Path) -> Path | None:
        directory = configured_path.parent
        if not directory.exists():
            return None
        candidates = sorted(directory.glob("*.docx"), key=lambda candidate: candidate.name.lower())
        return candidates[0].resolve() if candidates else None

    @property
    def resolved_sqlite_path(self) -> Path:
        if str(self.sqlite_path) == ":memory:":
            return Path(":memory:")
        if self.sqlite_path.is_absolute():
            return self.sqlite_path
        return (self.repo_root / self.sqlite_path).resolve()

    @property
    def resolved_profile_path(self) -> Path:
        configured = (
            self.profile_path
            if self.profile_path.is_absolute()
            else (self.repo_root / self.profile_path).resolve()
        )
        if configured.exists():
            return configured
        discovered = self._discover_profile_path(configured)
        return discovered or configured

    @property
    def resolved_resume_path(self) -> Path | None:
        configured = (
            self.resume_path
            if self.resume_path.is_absolute()
            else (self.repo_root / self.resume_path).resolve()
        )
        if configured.exists():
            return configured
        return self._discover_resume_path(configured)

    @property
    def resolved_raw_profile_path(self) -> Path:
        if self.raw_profile_path.is_absolute():
            return self.raw_profile_path
        return (self.repo_root / self.raw_profile_path).resolve()

    @property
    def resolved_profile_upload_dir(self) -> Path:
        if self.profile_upload_dir.is_absolute():
            return self.profile_upload_dir
        return (self.repo_root / self.profile_upload_dir).resolve()

    @property
    def resolved_profile_answers_path(self) -> Path:
        if self.profile_answers_path.is_absolute():
            return self.profile_answers_path
        return (self.repo_root / self.profile_answers_path).resolve()

    @property
    def resolved_external_accounts_path(self) -> Path:
        if self.external_accounts_path.is_absolute():
            return self.external_accounts_path
        return (self.repo_root / self.external_accounts_path).resolve()

    @property
    def resolved_target_profile_path(self) -> Path:
        source = self.resolved_profile_path
        configured = (
            source.with_name(f"{source.stem}.canonical{source.suffix}")
            if source.suffix
            else source.with_name(f"{source.name}.canonical.json")
        )
        discovered = self._discover_target_profile_path(configured)
        if not source.exists() and discovered is not None:
            return discovered
        if configured.exists():
            return configured
        return discovered or configured


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
