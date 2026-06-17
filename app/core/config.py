"""
Application configuration using pydantic-settings.

This module provides type-safe configuration management by loading
settings from environment variables and .env files. All settings
are validated at startup, catching configuration errors early.

Usage:
    from app.core.config import settings
    
    print(settings.APP_NAME)
    print(settings.DATABASE_URL)
"""

from functools import lru_cache
from typing import List

from cryptography.fernet import Fernet
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    
    Settings are loaded in this priority order:
    1. Environment variables
    2. .env file
    3. Default values defined here
    
    Attributes:
        APP_NAME: Display name for the application
        ENVIRONMENT: Current environment (development/staging/production)
        DEBUG: Enable debug mode and verbose logging
    """
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",  # Ignore extra env vars not defined here
    )
    
    # -------------------------------------------------------------------------
    # Application Settings
    # -------------------------------------------------------------------------
    APP_NAME: str = "ScrapeGPT"
    ENVIRONMENT: str = Field(default="development", pattern="^(development|staging|production)$")
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"
    
    # -------------------------------------------------------------------------
    # Server Settings
    # -------------------------------------------------------------------------
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    WORKERS: int = 1
    
    # -------------------------------------------------------------------------
    # Database Settings
    # -------------------------------------------------------------------------
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:password@localhost:5432/scrapegpt",
        description="PostgreSQL connection URL with asyncpg driver"
    )
    DB_POOL_SIZE: int = Field(default=5, ge=1, le=50)
    DB_MAX_OVERFLOW: int = Field(default=10, ge=0, le=100)
    
    # -------------------------------------------------------------------------
    # Security Settings
    # -------------------------------------------------------------------------
    SECRET_KEY: str = Field(
        default="change-this-secret-key-in-prod-32chars",
        min_length=32,
        description="Secret key for JWT signing. Generate with: openssl rand -hex 32"
    )
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=15, ge=5, le=1440)
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7, ge=1, le=30)
    PASSWORD_HASH_ROUNDS: int = Field(default=12, ge=4, le=31)
    
    # -------------------------------------------------------------------------
    # CORS Settings
    # -------------------------------------------------------------------------
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:8000,http://127.0.0.1:5173"
    
    @property
    def cors_origins_list(self) -> List[str]:
        """Parse CORS_ORIGINS string into a list of origins."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]
    
    PROVIDER_KEY_ENCRYPTION_SECRET: str = Field(
        ...,
        description=(
            "Fernet key used to encrypt stored provider API keys. "
            "Generate with: python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        ),
    )
    
    # -------------------------------------------------------------------------
    # Scraping Settings
    # -------------------------------------------------------------------------
    SCRAPE_TIMEOUT: int = Field(default=30, ge=5, le=300)
    LLM_TIMEOUT: int = Field(default=120, ge=10, le=600)
    READINESS_TIMEOUT_SECONDS: float = Field(default=2.0, ge=0.5, le=10.0)
    MAX_CONCURRENT_JOBS_PER_USER: int = Field(default=3, ge=1, le=50)
    MAX_PAGES_PER_JOB: int = Field(default=500, ge=1, le=100000)
    MAX_RECORDS_PER_PAGE: int = Field(
        default=1000,
        ge=1,
        le=10000,
        description=(
            "Maximum records extracted from a single page. "
            "Prevents runaway extraction on pages with very "
            "large repeated containers. Operator-level setting; "
            "not surfaced to users."
        ),
    )
    CRAWL_CONCURRENCY: int = Field(
        default=3, ge=1, le=50,
        description=(
            "Reserved for future use. Currently the extraction "
            "loop processes pages sequentially with MIN_CRAWL_DELAY_MS "
            "between fetches. This setting does not affect concurrency "
            "until a parallel crawl executor is implemented."
        ),
    )
    MIN_CRAWL_DELAY_MS: int = Field(default=500, ge=0, le=60000)
    JOB_QUEUE_DEPTH: int = Field(default=10, ge=1, le=1000)
    USER_AGENT: str = Field(
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        description="Browser User-Agent sent with every fetch. Override in .env to keep current.",
    )

    # -------------------------------------------------------------------------
    # Anti-Bot / Stealth Settings
    # -------------------------------------------------------------------------
    FLARESOLVERR_URL: str = Field(
        default="",
        description=(
            "Optional FlareSolverr instance URL (e.g. http://localhost:8191). "
            "When set, ScrapeGPT uses FlareSolverr as a last-resort fallback for "
            "Cloudflare JS challenges that survive camoufox/stealth-Playwright. "
            "Run with: docker run -d -p 8191:8191 flaresolverr/flaresolverr:latest"
        ),
    )
    FLARESOLVERR_TIMEOUT: int = Field(
        default=60,
        ge=10,
        le=300,
        description="Seconds to wait for FlareSolverr to solve a challenge.",
    )
    CAPSOLVER_API_KEY: str = Field(
        default="",
        description=(
            "Optional CapSolver API key for solving Cloudflare Turnstile and "
            "reCAPTCHA challenges automatically. Get one at capsolver.com."
        ),
    )

    # -------------------------------------------------------------------------
    # Watchdog Settings
    # -------------------------------------------------------------------------
    WATCHDOG_SCRAPING_TIMEOUT_MINUTES: int = Field(default=5, ge=1, le=30)
    WATCHDOG_LLM_TIMEOUT_MINUTES: int = Field(default=10, ge=2, le=60)
    WATCHDOG_PERMISSION_GRANTED_TIMEOUT_MINUTES: int = Field(
        default=3, ge=1, le=10,
        description="Timeout for tasks stuck in PERMISSION_GRANTED"
    )
    WATCHDOG_JOB_QUEUED_TIMEOUT_MINUTES: int = Field(
        default=3, ge=1, le=10,
        description="Timeout for jobs stuck in QUEUED"
    )
    WATCHDOG_JOB_ANALYZING_TIMEOUT_MINUTES: int = Field(
        default=5, ge=1, le=30,
        description="Timeout for jobs stuck in ANALYZING"
    )
    WATCHDOG_PROJECT_DISCOVERING_TIMEOUT_MINUTES: int = Field(
        default=10, ge=1, le=30,
        description="Timeout for projects stuck in DISCOVERING"
    )
    WATCHDOG_PROJECT_EXTRACTING_TIMEOUT_MINUTES: int = Field(
        default=10, ge=5, le=180,
        description=(
            "Timeout for projects stuck in EXTRACTING. "
            "Reduced from 60 to 10 minutes because in-process "
            "BackgroundTasks cannot survive a server restart — "
            "a shorter timeout surfaces crashed extractions faster."
        ),
    )
    WATCHDOG_PROJECT_EXPORTING_TIMEOUT_MINUTES: int = Field(
        default=10, ge=1, le=30,
        description="Timeout for projects stuck in EXPORTING"
    )
    WATCHDOG_MAX_RESUME_ATTEMPTS: int = Field(
        default=3, ge=0, le=20,
        description=(
            "How many times the watchdog re-dispatches a stalled extraction run "
            "(in-process worker died, e.g. server restart) before giving up and "
            "hard-failing the project with EXTRACTION_RESUME_EXHAUSTED. 0 "
            "disables resume — stalled runs are hard-failed immediately, the "
            "pre-A1 behavior."
        ),
    )

    # -------------------------------------------------------------------------
    # Job / Analysis Settings
    # -------------------------------------------------------------------------
    ALLOW_PRIVATE_NETWORK_URLS: bool = Field(
        default=False,
        description="Allow fetching private/localhost URLs (tests and dev only)"
    )
    MAX_FETCH_BYTES: int = Field(
        default=2 * 1024 * 1024,
        ge=1024,
        description="Maximum response body size for URL fetching"
    )
    MAX_REDIRECTS: int = Field(default=5, ge=0, le=20)
    ANALYSIS_CONFIDENCE_FAST_THRESHOLD: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        description="Minimum confidence for FAST workflow to land in ANALYSIS_READY vs AWAITING_SETUP"
    )
    ANALYSIS_CACHE_TTL_DAYS: int = Field(
        default=30,
        ge=0,
        le=365,
        description=(
            "Days before cached LLM analysis results expire. "
            "0 = no TTL (cache never expires, same as prior behavior). "
            "Stale cache entries are purged by the watchdog."
        ),
    )

    # -------------------------------------------------------------------------
    # Rate Limiting Settings
    # -------------------------------------------------------------------------
    RATE_LIMIT_PER_MINUTE: int = Field(
        default=60, ge=1, le=1000,
        description="Default rate limit per minute"
    )
    RATE_LIMIT_SCRAPE_PER_MINUTE: int = Field(
        default=10, ge=1, le=100,
        description="Rate limit for /scrape/start per minute"
    )
    RATE_LIMIT_AUTH_PER_MINUTE: int = Field(
        default=5, ge=1, le=30,
        description="Rate limit for auth endpoints per minute"
    )

    # -------------------------------------------------------------------------
    # Email (SMTP) — used to deliver password-reset codes
    # -------------------------------------------------------------------------
    SMTP_HOST: str = Field(
        default="",
        description="SMTP server host. Empty disables outbound email (reset codes are dev-logged instead).",
    )
    SMTP_PORT: int = Field(default=587, ge=1, le=65535)
    SMTP_USERNAME: str = Field(default="")
    SMTP_PASSWORD: str = Field(default="")
    SMTP_FROM_EMAIL: str = Field(
        default="",
        description="From address for outbound email. Falls back to SMTP_USERNAME when empty.",
    )
    SMTP_USE_TLS: bool = Field(default=True, description="Use STARTTLS for the SMTP connection.")
    PASSWORD_RESET_CODE_TTL_MINUTES: int = Field(
        default=15,
        ge=1,
        le=120,
        description="Minutes a password-reset code remains valid before it expires.",
    )

    # -------------------------------------------------------------------------
    # Logging Settings
    # -------------------------------------------------------------------------
    LOG_LEVEL: str = Field(
        default="INFO",
        pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$"
    )
    LOG_FORMAT: str = Field(default="text", pattern="^(json|text)$")
    
    # -------------------------------------------------------------------------
    # Computed Properties
    # -------------------------------------------------------------------------
    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.ENVIRONMENT == "production"
    
    @property
    def is_development(self) -> bool:
        """Check if running in development environment."""
        return self.ENVIRONMENT == "development"

    @property
    def smtp_configured(self) -> bool:
        """Whether an SMTP server is configured for outbound email."""
        return bool(self.SMTP_HOST)

    @property
    def password_reset_enabled(self) -> bool:
        """Whether the password-reset flow is usable.

        Enabled when SMTP is configured (codes are emailed), or in development
        (codes are logged to the server console instead of emailed). When
        neither holds, the frontend hides the flow so users do not request a
        code they can never receive.
        """
        return self.smtp_configured or self.is_development
    
    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, v: str) -> str:
        """Warn if using default secret key in non-development."""
        if v == "change-this-secret-key-in-prod-32chars":
            import warnings
            warnings.warn(
                "Using default SECRET_KEY! Generate a secure key for production.",
                UserWarning,
                stacklevel=2
            )
        return v

    @field_validator("PROVIDER_KEY_ENCRYPTION_SECRET")
    @classmethod
    def validate_provider_key_encryption_secret(cls, v: str) -> str:
        """Validate provider key encryption secret at startup."""
        try:
            Fernet(v.encode("utf-8"))
        except Exception as exc:
            raise ValueError(
                "PROVIDER_KEY_ENCRYPTION_SECRET is missing or invalid. "
                "Generate one: python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\""
            ) from exc
        return v


@lru_cache
def get_settings() -> Settings:
    """
    Get cached settings instance.
    
    Using lru_cache ensures settings are only loaded once per process,
    improving performance and ensuring consistency.
    
    Returns:
        Settings: The application settings instance
    """
    return Settings()


# Global settings instance for convenient access
settings = get_settings()
