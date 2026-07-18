"""
bot/admin/config.py — конфігурація адмін-бота.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class AdminBotConfig:
    token:     str
    admin_ids: set[int]

    @classmethod
    def from_env(cls) -> "AdminBotConfig":
        token = os.environ.get("ADMIN_BOT_TOKEN", "")
        if not token:
            raise RuntimeError("ADMIN_BOT_TOKEN не задано")

        raw = os.environ.get("ADMIN_IDS", "")
        ids: set[int] = {int(p.strip()) for p in raw.split(",") if p.strip().isdigit()}
        if not ids:
            raise RuntimeError("ADMIN_IDS не задано або некоректне (через кому)")

        return cls(token=token, admin_ids=ids)