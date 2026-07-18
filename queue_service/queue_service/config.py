from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    account_service_url: str = os.getenv("ACCOUNT_SERVICE_URL", "http://account-service:8100")

    # Власна черга задач цього сервісу — НЕ account_events:* (те зарезервовано
    # під socket-події з account-service). Свій namespace, щоб не перетнутись
    # з іншими сервісами, які теж читають той самий Redis.
    task_queue_key: str = os.getenv("TASK_QUEUE_KEY", "queue_service:tasks")

    db_path: str = os.getenv("QUEUE_SERVICE_DB_PATH", "/data/queue_service.db")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
