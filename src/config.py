import os

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Polytsia"
    debug: bool = False

    # Security
    secret_key: str
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30

    # Database
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "polytsia"

    # Redis
    redis_host: str = "redis"
    redis_port: int = 6379

    # External ports
    postgres_external_port: int = 15432
    redis_external_port: int = 16379

    # External APIs
    tmdb_api_key: str = ""
    rawg_api_key: str = ""

    def _build_db_url(self, db_name: str, host: str, port: int) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{host}:{port}/{db_name}"
        )

    def _get_db_connection_params(self) -> tuple[str, int]:
        if os.path.exists("/.dockerenv"):
            return self.postgres_host, self.postgres_port
        return "localhost", self.postgres_external_port

    def _get_redis_connection_params(self) -> tuple[str, int]:
        if os.path.exists("/.dockerenv"):
            return self.redis_host, self.redis_port
        return "localhost", self.redis_external_port

    @computed_field
    @property
    def database_url(self) -> str:
        host, port = self._get_db_connection_params()
        return self._build_db_url(self.postgres_db, host, port)

    @computed_field
    @property
    def test_database_url(self) -> str:
        host, port = self._get_db_connection_params()
        return self._build_db_url(f"{self.postgres_db}_test", host, port)

    @computed_field
    @property
    def redis_url(self) -> str:
        host, port = self._get_redis_connection_params()
        return f"redis://{host}:{port}/0"

    @computed_field
    @property
    def celery_broker_url(self) -> str:
        host, port = self._get_redis_connection_params()
        return f"redis://{host}:{port}/1"


settings = Settings()
