from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SUPPORTED_DB_SCHEMES = (
    "postgresql://",
    "postgresql+psycopg://",
    "postgresql+psycopg2://",
    "postgres://",
    "sqlite://",
    "sqlite+pysqlite://",
)


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Defaults are intended for local development only. Production deployments
    must provide real values through the environment.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Service metadata
    service_name: str = "ai-code-review-api"
    version: str = "0.1.0"

    # Infrastructure connections (safe local defaults)
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/code_review"
    REDIS_URL: str = "redis://localhost:6379/0"
    CHROMA_URL: str = "http://localhost:8001"

    # Database connection pool tuning
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_RECYCLE: int = 1800

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("DATABASE_URL must not be empty")
        if not value.startswith(_SUPPORTED_DB_SCHEMES):
            supported = ", ".join(_SUPPORTED_DB_SCHEMES)
            raise ValueError(
                f"DATABASE_URL must use a supported scheme ({supported})"
            )
        return value

    # GitHub App integration (no safe defaults for secrets)
    GITHUB_APP_ID: str = ""
    GITHUB_PRIVATE_KEY: str = ""
    GITHUB_WEBHOOK_SECRET: str = ""
    GITHUB_API_URL: str = "https://api.github.com"

    # OpenAI integration
    OPENAI_API_KEY: str = ""

    # OpenTelemetry / Jaeger tracing
    OTEL_ENABLED: bool = True
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://jaeger:4317"
    OTEL_EXPORTER_OTLP_INSECURE: bool = True
    OTEL_SERVICE_NAME: str = ""

    def require_github_app_credentials(self) -> None:
        """Ensure GitHub App credentials are configured before use.

        Secrets intentionally have empty defaults so the app can boot for local
        development without them. Authentication code calls this guard so a
        clear error is raised the moment credentials are actually required.
        """
        missing = [
            name
            for name in ("GITHUB_APP_ID", "GITHUB_PRIVATE_KEY")
            if not str(getattr(self, name) or "").strip()
        ]
        if missing:
            raise RuntimeError(
                "Missing required GitHub App settings: "
                + ", ".join(missing)
                + ". Set them via environment variables."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
