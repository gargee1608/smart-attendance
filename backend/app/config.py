from functools import lru_cache
import base64

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    smartattendance_env: str = "development"
    database_url: str = "postgresql+psycopg://smartattendance_app:devappdb@postgres:5432/smartattendance"
    admin_database_url: str = "postgresql+psycopg://smartattendance_admin:devadmindb@postgres:5432/smartattendance"
    migration_database_url: str = "postgresql+psycopg://postgres:devpostgres@postgres:5432/smartattendance"
    redis_url: str = "redis://redis:6379/0"
    jwt_secret: str = "dev-only-jwt-secret-change-me"
    jwt_ttl_hours: int = 12
    file_encryption_key: str = "ZGV2LW9ubHktZmlsZS1rZXktbm90LWZvci1wcm9kISE="
    storage_root: str = "/data/storage"
    face_engine_url: str = "http://face-engine:8090"
    ai_service_url: str = "http://ai-service:8091"
    face_match_threshold: float = 0.363
    face_ambiguity_margin: float = 0.05
    unknown_group_threshold: float = 0.55
    min_quality: float = 0.45
    app_db_password: str = "devappdb"
    admin_db_password: str = "devadmindb"
    initial_admin_email: str = "admin@smartattendance.local"
    initial_admin_password: str = "ChangeMe-Admin-1"
    ollama_model: str = "qwen2.5:7b"
    public_base_url: str = "http://localhost:18480"

    def encryption_key_bytes(self) -> bytes:
        try:
            raw = base64.b64decode(self.file_encryption_key)
        except Exception as exc:
            raise ValueError("FILE_ENCRYPTION_KEY is not valid base64") from exc
        if len(raw) != 32:
            raise ValueError("FILE_ENCRYPTION_KEY must decode to 32 bytes")
        return raw

    def assert_production_safe(self) -> None:
        if self.smartattendance_env != "production":
            return
        problems = []
        if self.jwt_secret.startswith("dev-only") or len(self.jwt_secret) < 24:
            problems.append("JWT_SECRET")
        if self.initial_admin_password.startswith("ChangeMe") or len(self.initial_admin_password) < 12:
            problems.append("INITIAL_ADMIN_PASSWORD")
        if self.app_db_password.startswith("dev") or len(self.app_db_password) < 12:
            problems.append("APP_DB_PASSWORD")
        if self.admin_db_password.startswith("dev") or len(self.admin_db_password) < 12:
            problems.append("ADMIN_DB_PASSWORD")
        if "devpostgres" in self.migration_database_url:
            problems.append("POSTGRES_PASSWORD")
        try:
            self.encryption_key_bytes()
        except ValueError:
            problems.append("FILE_ENCRYPTION_KEY")
        if problems:
            raise RuntimeError(
                "Refusing to start in production with default or weak secrets: " + ", ".join(problems)
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
