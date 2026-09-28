from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Backend configuration, loaded from backend/.env.

    Nothing here is ever sent to a browser.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str
    supabase_publishable_key: str

    # Bypasses Row Level Security completely. Treat as root.
    supabase_service_role_key: str = ""

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"

    ai_confidence_threshold: float = 0.75
    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def ai_enabled(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def admin_enabled(self) -> bool:
        return bool(self.supabase_service_role_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
