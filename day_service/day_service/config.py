"""
day_service/config.py — конфігурація сервісу з env.

day-service навмисно НЕ знає нічого про daily-бонус, mining, quiz тощо —
його єдина відповідальність: для кожного зареєстрованого account_id
визначити момент "настав новий день" (з урахуванням індивідуального
зсуву по часу для акаунта) і один раз про це оповістити. Що саме бізнес
робить з цією подією — не його справа.

Немає HTTP-сервера: реєстрація акаунтів і оповіщення про новий день
йдуть через Redis (список команд на вхід, pub/sub на вихід) — щоб не
тягнути в контейнер, який 99.9% часу просто спить, цілий web-стек
(FastAPI/uvicorn/pydantic).
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_path: str = os.getenv("DAY_SERVICE_DB_PATH", "/data/day_service.db")

    redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379/0")

    # Список-черга команд (register/unregister/status/list/force), яку
    # day-service вичитує через BRPOP. LIST у Redis, на відміну від
    # pub/sub, не губить повідомлення, якщо day-service на мить
    # недоступний — команда просто чекає в черзі до наступного BRPOP.
    commands_key: str = os.getenv("DAY_SERVICE_COMMANDS_KEY", "day_service:commands")

    # Канал сповіщень "настав новий день" (pub/sub — fire-and-forget,
    # навмисно: DailyMonitor і так підстраховується catch-up-перевіркою
    # при attach(), тому гарантована доставка тут не потрібна і не варта
    # додаткової інфраструктури).
    events_prefix: str = os.getenv("DAY_EVENTS_PREFIX", "day_service_events")

    # Часова зона, у якій day-service рахує "календарний день".
    # Має збігатись із таймзоною бізнес-застосунку, інакше межі доби
    # для двох сервісів розʼїдуться.
    timezone: str = os.getenv("TZ", "Europe/Kiev")

    # Дефолти для акаунтів, які реєструються без явного base_time/jitter.
    default_base_time: str = os.getenv("DAY_SERVICE_DEFAULT_BASE_TIME", "04:30")
    default_jitter_minutes: int = int(os.getenv("DAY_SERVICE_DEFAULT_JITTER_MINUTES", "180"))

    log_level: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
