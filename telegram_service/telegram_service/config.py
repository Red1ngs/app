"""
telegram_service/config.py — конфігурація Telegram-сервісу.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class TelegramBotConfig:
    token:            str
    admin_ids:        set[int]
    core_service_url: str
    core_service_token: str

    @classmethod
    def from_env(cls) -> "TelegramBotConfig":
        token = os.environ.get("ADMIN_BOT_TOKEN", "")
        if not token:
            raise RuntimeError("ADMIN_BOT_TOKEN не задано")

        raw = os.environ.get("ADMIN_IDS", "")
        ids: set[int] = {int(p.strip()) for p in raw.split(",") if p.strip().isdigit()}
        if not ids:
            raise RuntimeError("ADMIN_IDS не задано або некоректне (через кому)")

        core_url = os.environ.get("CORE_SERVICE_URL", "http://core-service:8200")
        core_token = os.environ.get("CORE_SERVICE_TOKEN", "")

        return cls(
            token=token,
            admin_ids=ids,
            core_service_url=core_url,
            core_service_token=core_token,
        )
