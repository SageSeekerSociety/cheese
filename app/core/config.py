from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="Cheese Backend (Python)", alias="APP_NAME")
    environment: str = Field(default="development", alias="ENVIRONMENT")

    # Frontend URL (used for building links like password reset)
    frontend_url: str = Field(default="http://localhost:3000", alias="FRONTEND_URL")

    # CORS configuration
    cors_origins: str = Field(
        default="http://localhost:5173,http://localhost:3000", alias="CORS_ORIGINS"
    )
    cors_credentials: bool = Field(default=True, alias="CORS_CREDENTIALS")

    # Base URL for avatars (can be this backend or CDN)
    avatar_base_url: str = Field(default="http://localhost:8081", alias="AVATAR_BASE_URL")

    # Database
    database_url: str = Field(
        default="postgresql+psycopg2://postgres:postgres@localhost:5432/cheese",
        alias="DATABASE_URL",
    )

    # JWT settings for Python auth flow
    jwt_secret: str = Field(default="dev-secret", alias="JWT_SECRET")
    access_token_expires_seconds: int = Field(default=15 * 60, alias="ACCESS_TOKEN_EXPIRES_SECONDS")
    refresh_token_expires_seconds: int = Field(
        default=60 * 60 * 24 * 30, alias="REFRESH_TOKEN_EXPIRES_SECONDS"
    )

    # Encryption
    realname_encryption_key: str = Field(
        default="",
        alias="REALNAME_ENCRYPTION_KEY",
        description="Base64 Fernet key for user real-name encryption; auto-derived when blank.",
    )

    # Redis + notification infrastructure
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    notification_dedup_ttl_seconds: int = Field(
        default=10 * 60, alias="NOTIFICATION_DEDUP_TTL_SECONDS"
    )
    notification_email_queue_key: str = Field(
        default="cheese:notifications:email", alias="NOTIFICATION_EMAIL_QUEUE_KEY"
    )
    notification_email_batch_size: int = Field(default=100, alias="NOTIFICATION_EMAIL_BATCH_SIZE")
    notification_aggregation_finalize_interval_seconds: int = Field(
        default=60, alias="NOTIFICATION_AGGREGATION_FINALIZE_INTERVAL_SECONDS"
    )

    # Invite code registration
    require_invite_code: bool = Field(default=False, alias="REQUIRE_INVITE_CODE")

    # Rank / eligibility related flags (soft-aligned with Kotlin ApplicationConfig)
    rank_check_enforced: bool = Field(default=False, alias="APPLICATION_RANK_CHECK_ENFORCED")
    rank_jump: int = Field(default=1, alias="APPLICATION_RANK_JUMP")
    enforce_task_participant_limit_check: bool = Field(
        default=False, alias="APPLICATION_ENFORCE_TASK_PARTICIPANT_LIMIT_CHECK"
    )

    # Storage configuration
    storage_type: str = Field(default="local", alias="STORAGE_TYPE")
    storage_local_path: str = Field(default="./uploads", alias="STORAGE_LOCAL_PATH")
    storage_local_url: str = Field(default="/uploads", alias="STORAGE_LOCAL_URL")
    s3_bucket: str = Field(default="cheese", alias="S3_BUCKET")
    s3_endpoint_url: str = Field(default="", alias="S3_ENDPOINT_URL")
    s3_access_key: str = Field(default="", alias="S3_ACCESS_KEY")
    s3_secret_key: str = Field(default="", alias="S3_SECRET_KEY")
    s3_region: str = Field(default="us-east-1", alias="S3_REGION")
    s3_public_url: str = Field(default="", alias="S3_PUBLIC_URL")

    # Email / SMTP configuration
    email_smtp_host: str = Field(default="", alias="EMAIL_SMTP_HOST")
    email_smtp_port: int = Field(default=587, alias="EMAIL_SMTP_PORT")
    email_smtp_username: str = Field(default="", alias="EMAIL_SMTP_USERNAME")
    email_smtp_password: str = Field(default="", alias="EMAIL_SMTP_PASSWORD")
    email_from_address: str = Field(default="", alias="EMAIL_FROM_ADDRESS")
    email_smtp_ssl: bool = Field(default=False, alias="EMAIL_SMTP_SSL_ENABLE")

    # OpenAI / LLM configuration
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    openai_default_model: str = Field(default="gpt-4o-mini", alias="OPENAI_DEFAULT_MODEL")
    openai_reasoning_model: str = Field(default="o1-mini", alias="OPENAI_REASONING_MODEL")
    openai_temperature: float = Field(default=0.7, alias="OPENAI_TEMPERATURE")
    openai_max_tokens: int = Field(default=4096, alias="OPENAI_MAX_TOKENS")
    openai_timeout_seconds: float = Field(default=180.0, alias="OPENAI_TIMEOUT_SECONDS")
    openai_pdf_timeout_seconds: float = Field(
        default=300.0,
        alias="OPENAI_PDF_TIMEOUT_SECONDS",
    )
    ai_daily_quota: float = Field(default=10.0, alias="AI_DAILY_QUOTA")

    # WebAuthn / Passkey configuration
    webauthn_rp_id: str = Field(default="localhost", alias="WEBAUTHN_RP_ID")
    webauthn_rp_name: str = Field(default="Cheese Community", alias="WEBAUTHN_RP_NAME")
    webauthn_origin: str = Field(default="http://localhost:5173", alias="WEBAUTHN_ORIGIN")

    # OAuth configuration
    oauth_enabled_providers: str = Field(default="", alias="OAUTH_ENABLED_PROVIDERS")
    oauth_github_client_id: str = Field(default="", alias="OAUTH_GITHUB_CLIENT_ID")
    oauth_github_client_secret: str = Field(default="", alias="OAUTH_GITHUB_CLIENT_SECRET")
    oauth_github_redirect_url: str = Field(default="", alias="OAUTH_GITHUB_REDIRECT_URL")
    oauth_google_client_id: str = Field(default="", alias="OAUTH_GOOGLE_CLIENT_ID")
    oauth_google_client_secret: str = Field(default="", alias="OAUTH_GOOGLE_CLIENT_SECRET")
    oauth_google_redirect_url: str = Field(default="", alias="OAUTH_GOOGLE_REDIRECT_URL")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
