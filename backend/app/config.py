from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

EnvironmentName = Literal["dev", "test", "prod"]
AiProviderName = Literal["none", "openai"]
DatabaseAuthMode = Literal["password", "managed_identity"]
ApiAuthMode = Literal["off", "entra"]
RuntimeMode = Literal["demo", "local", "enterprise"]
DataProviderName = Literal["static", "local_api", "enterprise_api"]
AuthMode = Literal["off", "local_dev", "oidc"]
ArtifactProviderName = Literal["public_static", "local_file", "object_storage"]
JobProviderName = Literal["inline", "external_worker"]
BACKEND_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
ROOT_BACKEND_ENV_FILE = Path(__file__).resolve().parents[2] / "backend.env"
LOCAL_FRONTEND_CORS_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
    "http://localhost:3003",
    "http://127.0.0.1:3003",
)


class Settings(BaseSettings):
    """Environment-driven API settings.

    `POSTGRES_PASSWORD` is preferred for backend deployments. The local CFS
    pipeline password variable remains supported so the first backend scaffold
    can connect to the existing development PostGIS database without copying
    secrets.
    """

    app_env: EnvironmentName = Field(
        default="dev",
        validation_alias=AliasChoices("APP_ENV", "CFS_API_ENV"),
    )
    cfs_runtime_mode: RuntimeMode = Field(
        default="local",
        validation_alias=AliasChoices("CFS_RUNTIME_MODE"),
    )
    cfs_data_provider: DataProviderName = Field(
        default="local_api",
        validation_alias=AliasChoices("CFS_DATA_PROVIDER"),
    )
    cfs_auth_mode: AuthMode = Field(
        default="local_dev",
        validation_alias=AliasChoices("CFS_AUTH_MODE", "CFS_API_AUTH_MODE"),
    )
    cfs_artifact_provider: ArtifactProviderName = Field(
        default="local_file",
        validation_alias=AliasChoices("CFS_ARTIFACT_PROVIDER"),
    )
    cfs_job_provider: JobProviderName = Field(
        default="inline",
        validation_alias=AliasChoices("CFS_JOB_PROVIDER"),
    )
    cfs_presentation_cache_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("CFS_PRESENTATION_CACHE_ENABLED"),
    )
    cfs_presentation_freeze_id: str = Field(
        default="unfrozen",
        validation_alias=AliasChoices("CFS_PRESENTATION_FREEZE_ID"),
    )
    cfs_live_refresh_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("CFS_LIVE_REFRESH_ENABLED"),
    )
    cfs_live_refresh_timeout_seconds: int = Field(
        default=900,
        ge=30,
        le=3600,
        validation_alias=AliasChoices("CFS_LIVE_REFRESH_TIMEOUT_SECONDS"),
    )
    postgres_host: str = Field(
        default="localhost",
        validation_alias=AliasChoices("POSTGRES_HOST"),
    )
    postgres_port: int = Field(
        default=5433,
        validation_alias=AliasChoices("POSTGRES_PORT"),
    )
    postgres_db: str = Field(
        default="cfs_dev",
        validation_alias=AliasChoices("POSTGRES_DB"),
    )
    postgres_user: str = Field(
        default="postgres",
        validation_alias=AliasChoices("POSTGRES_USER"),
    )
    postgres_password: str = Field(
        default="",
        validation_alias=AliasChoices("POSTGRES_PASSWORD", "CFS_POSTGRES_PASSWORD"),
    )
    database_url: str = Field(
        default="",
        validation_alias=AliasChoices("DATABASE_URL"),
    )
    database_auth_mode: DatabaseAuthMode = Field(
        default="password",
        validation_alias=AliasChoices("CFS_DATABASE_AUTH_MODE"),
    )
    azure_postgres_host: str = Field(
        default="",
        validation_alias=AliasChoices("CFS_AZURE_POSTGRES_HOST"),
    )
    azure_postgres_database: str = Field(
        default="",
        validation_alias=AliasChoices("CFS_AZURE_POSTGRES_DATABASE"),
    )
    azure_postgres_port: int = Field(
        default=5432,
        validation_alias=AliasChoices("CFS_AZURE_POSTGRES_PORT"),
    )
    azure_postgres_user: str = Field(
        default="",
        validation_alias=AliasChoices("CFS_AZURE_POSTGRES_USER"),
    )
    azure_client_id: str = Field(
        default="",
        validation_alias=AliasChoices("AZURE_CLIENT_ID", "CFS_AZURE_CLIENT_ID"),
    )
    database_connect_timeout_seconds: int = Field(
        default=5,
        validation_alias=AliasChoices(
            "DATABASE_CONNECT_TIMEOUT_SECONDS",
            "DB_CONNECT_TIMEOUT_SECONDS",
        ),
    )
    database_pool_size: int = Field(
        default=2,
        ge=1,
        le=10,
        validation_alias=AliasChoices("CFS_DATABASE_POOL_SIZE", "SQLALCHEMY_POOL_SIZE"),
    )
    database_max_overflow: int = Field(
        default=1,
        ge=0,
        le=10,
        validation_alias=AliasChoices("CFS_DATABASE_MAX_OVERFLOW", "SQLALCHEMY_MAX_OVERFLOW"),
    )
    database_pool_timeout_seconds: int = Field(
        default=10,
        ge=1,
        le=30,
        validation_alias=AliasChoices("CFS_DATABASE_POOL_TIMEOUT_SECONDS", "SQLALCHEMY_POOL_TIMEOUT_SECONDS"),
    )
    database_pool_recycle_seconds: int = Field(
        default=2700,
        ge=300,
        le=3300,
        validation_alias=AliasChoices("CFS_DATABASE_POOL_RECYCLE_SECONDS", "SQLALCHEMY_POOL_RECYCLE_SECONDS"),
    )
    database_statement_timeout_ms: int = Field(
        default=3000,
        validation_alias=AliasChoices(
            "DATABASE_STATEMENT_TIMEOUT_MS",
            "DB_STATEMENT_TIMEOUT_MS",
        ),
    )
    sqlalchemy_echo: bool = Field(
        default=False,
        validation_alias=AliasChoices("SQLALCHEMY_ECHO"),
    )
    cors_allowed_origins: str = Field(
        default=",".join(LOCAL_FRONTEND_CORS_ORIGINS),
        validation_alias=AliasChoices(
            "CFS_CORS_ORIGINS",
            "CFS_CORS_ALLOWED_ORIGINS",
            "CORS_ALLOWED_ORIGINS",
        ),
    )
    cfs_enable_docs: bool = Field(
        default=True,
        validation_alias=AliasChoices("CFS_ENABLE_DOCS"),
    )
    cfs_staging_protect_api: bool = Field(
        default=False,
        validation_alias=AliasChoices("CFS_STAGING_PROTECT_API"),
    )
    cfs_staging_access_token: str = Field(
        default="",
        validation_alias=AliasChoices("CFS_STAGING_ACCESS_TOKEN"),
    )
    cfs_entra_tenant_id: str = Field(
        default="",
        validation_alias=AliasChoices("CFS_ENTRA_TENANT_ID"),
    )
    cfs_entra_api_audience: str = Field(
        default="",
        validation_alias=AliasChoices("CFS_ENTRA_API_AUDIENCE"),
    )
    cfs_organization_id: str = Field(
        default="",
        validation_alias=AliasChoices("CFS_ORGANIZATION_ID"),
    )
    cfs_entra_required_scope: str = Field(
        default="CFS.Access",
        validation_alias=AliasChoices("CFS_ENTRA_REQUIRED_SCOPE"),
    )
    cfs_entra_write_role: str = Field(
        default="",
        validation_alias=AliasChoices("CFS_ENTRA_WRITE_ROLE"),
    )
    cfs_entra_admin_role: str = Field(
        default="",
        validation_alias=AliasChoices("CFS_ENTRA_ADMIN_ROLE"),
    )
    cfs_entra_allowed_object_ids: str = Field(
        default="",
        validation_alias=AliasChoices("CFS_ENTRA_ALLOWED_OBJECT_IDS"),
    )
    cfs_model_lab_summary_cache_ttl_seconds: int = Field(
        default=21600,
        ge=0,
        le=86400,
        validation_alias=AliasChoices("CFS_MODEL_LAB_SUMMARY_CACHE_TTL_SECONDS"),
    )
    cfs_telemetry_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("CFS_TELEMETRY_ENABLED"),
    )
    applicationinsights_connection_string: str = Field(
        default="",
        validation_alias=AliasChoices("APPLICATIONINSIGHTS_CONNECTION_STRING"),
    )
    cfs_ai_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("CFS_AI_ENABLED"),
    )
    cfs_ai_provider: AiProviderName = Field(
        default="none",
        validation_alias=AliasChoices("CFS_AI_PROVIDER"),
    )
    cfs_ai_model: str = Field(
        default="",
        validation_alias=AliasChoices("CFS_AI_MODEL"),
    )
    cfs_ai_provider_timeout_seconds: float = Field(
        default=6.0,
        gt=0,
        le=20,
        validation_alias=AliasChoices("CFS_AI_PROVIDER_TIMEOUT_SECONDS"),
    )
    openai_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("OPENAI_API_KEY"),
    )
    cfs_eagleview_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("CFS_EAGLEVIEW_API_KEY"),
    )
    cfs_eagleview_secret_key: str = Field(
        default="",
        validation_alias=AliasChoices("CFS_EAGLEVIEW_SECRET_KEY"),
    )
    cfs_eagleview_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=30,
        validation_alias=AliasChoices("CFS_EAGLEVIEW_TIMEOUT_SECONDS"),
    )
    county_tax_rate_per_100: float = Field(
        default=0.57,
        validation_alias=AliasChoices("CFS_COUNTY_TAX_RATE_PER_100"),
    )
    model_config = SettingsConfigDict(
        env_file=(BACKEND_ENV_FILE, ROOT_BACKEND_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("cfs_data_provider", mode="before")
    @classmethod
    def normalize_legacy_data_provider(cls, value: object) -> object:
        return {
            "local_postgis": "local_api",
            "enterprise_service": "enterprise_api",
        }.get(value, value)

    @field_validator("cfs_auth_mode", mode="before")
    @classmethod
    def normalize_legacy_auth_mode(cls, value: object) -> object:
        return "oidc" if value == "entra" else value

    @model_validator(mode="after")
    def validate_runtime_provider_matrix(self) -> "Settings":
        allowed = {
            "demo": ({"static"}, {"off"}, {"public_static"}, {"inline"}),
            "local": (
                {"static", "local_api"},
                {"off", "local_dev", "oidc"},
                {"public_static", "local_file"},
                {"inline"},
            ),
            "enterprise": (
                {"enterprise_api"},
                {"oidc"},
                {"object_storage"},
                {"external_worker"},
            ),
        }
        providers, auth_modes, artifact_providers, job_providers = allowed[self.cfs_runtime_mode]
        values = (
            ("CFS_DATA_PROVIDER", self.cfs_data_provider, providers),
            ("CFS_AUTH_MODE", self.cfs_auth_mode, auth_modes),
            ("CFS_ARTIFACT_PROVIDER", self.cfs_artifact_provider, artifact_providers),
            ("CFS_JOB_PROVIDER", self.cfs_job_provider, job_providers),
        )
        invalid = [f"{name}={value}" for name, value, choices in values if value not in choices]
        if invalid:
            raise ValueError(
                f"Invalid {self.cfs_runtime_mode} runtime configuration: {', '.join(invalid)}."
            )
        if self.cfs_runtime_mode == "enterprise":
            required = {
                "CFS_ENTRA_TENANT_ID": self.cfs_entra_tenant_id,
                "CFS_ENTRA_API_AUDIENCE": self.cfs_entra_api_audience,
                "CFS_ORGANIZATION_ID": self.cfs_organization_id,
            }
            missing = [name for name, value in required.items() if not value.strip()]
            if missing:
                raise ValueError(
                    f"Enterprise runtime requires: {', '.join(missing)}."
                )
            origins = [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]
            invalid_origins = [origin for origin in origins if not _is_enterprise_origin(origin)]
            if not origins or invalid_origins:
                raise ValueError(
                    "Enterprise runtime requires explicit exact HTTPS CFS_CORS_ORIGINS; "
                    "wildcards, loopback hosts, paths, queries, and fragments are not allowed."
                )
        if self.cfs_runtime_mode == "demo" and self.cfs_ai_provider != "none":
            raise ValueError("Demo runtime requires CFS_AI_PROVIDER=none.")
        if self.cfs_ai_enabled and self.cfs_ai_provider == "none":
            raise ValueError("CFS_AI_ENABLED=true requires CFS_AI_PROVIDER=openai.")
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "prod"

    @property
    def cors_origin_list(self) -> list[str]:
        origins = [
            origin.strip()
            for origin in self.cors_allowed_origins.split(",")
            if origin.strip()
        ]

        if self.is_production:
            # Production deployments must opt into explicit origins; never carry
            # a permissive wildcard from a local/dev environment by accident.
            return [origin for origin in origins if origin != "*"]

        return origins

    @property
    def docs_enabled(self) -> bool:
        return self.cfs_enable_docs

    @property
    def staging_protection_enabled(self) -> bool:
        return self.cfs_staging_protect_api or bool(self.cfs_staging_access_token)

    @property
    def entra_auth_enabled(self) -> bool:
        return self.cfs_auth_mode == "oidc"

    @property
    def cfs_api_auth_mode(self) -> ApiAuthMode:
        """Legacy compatibility for existing Entra middleware and deployment checks."""

        return "entra" if self.cfs_auth_mode == "oidc" else "off"

    @property
    def entra_allowed_object_id_set(self) -> set[str]:
        return {
            value.strip().lower()
            for value in self.cfs_entra_allowed_object_ids.split(",")
            if value.strip()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _is_enterprise_origin(origin: str) -> bool:
    if "*" in origin:
        return False
    try:
        parsed = urlsplit(origin)
        host = parsed.hostname or ""
        parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return False
    normalized_host = host.rstrip(".").casefold()
    if normalized_host == "localhost" or normalized_host.endswith(".localhost"):
        return False
    try:
        address = ip_address(normalized_host)
        mapped = getattr(address, "ipv4_mapped", None)
        return not (address.is_loopback or (mapped is not None and mapped.is_loopback))
    except ValueError:
        return True
