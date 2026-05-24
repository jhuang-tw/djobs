"""Application settings loaded from environment variables."""

import os

class Settings:
    def __init__(self):
        self.debug = os.getenv("DEBUG", "false").lower() == "true"
        self.database_url = os.getenv("DATABASE_URL", "sqlite:///app.db")
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        self.secret_key = os.getenv("SECRET_KEY", "change-me-in-production")
        self.max_connections = int(os.getenv("MAX_CONNECTIONS", "10"))
        self.log_level = os.getenv("LOG_LEVEL", "INFO")

    def is_production(self) -> bool:
        return not self.debug

    def validate(self) -> list[str]:
        errors = []
        if self.secret_key == "change-me-in-production" and self.is_production():
            errors.append("SECRET_KEY must be set in production")
        if self.max_connections < 1:
            errors.append("MAX_CONNECTIONS must be positive")
        return errors
