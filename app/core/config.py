from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql+asyncpg://payment:payment@postgres:5432/payment_db"
    RABBITMQ_URL: str = "amqp://guest:guest@rabbitmq:5672/"
    API_KEY: str = "supersecretapikey"
    OUTBOX_INTERVAL: int = 5


settings = Settings()
