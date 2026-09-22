"""Environment configuration shared by every service.

Every service reads the same variable names. The values differ per container
(set in docker-compose.yml), the names never do.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=True)

    SERVICE_NAME: str = "service"
    SERVICE_PORT: int = 8000

    # postgresql+asyncpg://user:pass@host:5432/dbname
    DATABASE_URL: str = ""
    # Read replica. Empty string => fall back to DATABASE_URL (used by Market Data only).
    DATABASE_REPLICA_URL: str = ""

    REDIS_URL: str = "redis://redis:6379/0"
    RABBITMQ_URL: str = "amqp://guest:guest@rabbitmq:5672/"

    JWT_SECRET: str = "dev-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440  # 24h — demo friendly, documented in README

    # Shared secret guarding /internal/* endpoints. The gateway NEVER forwards
    # /internal/ paths, so only in-cluster callers can reach them.
    INTERNAL_API_KEY: str = "internal-dev-key"

    # Base URLs for synchronous service-to-service calls (Docker DNS names).
    USER_SERVICE_URL: str = "http://user-service:8001"
    ACCOUNT_SERVICE_URL: str = "http://account-service:8002"
    ORDER_SERVICE_URL: str = "http://order-service:8003"
    MATCHING_ENGINE_URL: str = "http://matching-engine:8004"
    MARKET_DATA_SERVICE_URL: str = "http://market-data-service:8005"
    PORTFOLIO_SERVICE_URL: str = "http://portfolio-service:8006"
    NOTIFICATION_SERVICE_URL: str = "http://notification-service:8007"

    # Browser origins allowed to call the gateway. Comma-separated. The default
    # is the documented frontend port; hosts that publish the frontend elsewhere
    # (e.g. Windows reserves 3000) must add that origin or every browser call
    # fails preflight with "No 'Access-Control-Allow-Origin' header".
    CORS_ORIGINS: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    LOG_LEVEL: str = "INFO"


settings = Settings()
