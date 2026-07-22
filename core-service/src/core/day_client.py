"""
src/core/day_client.py — клієнт до day-service (порт).

day-service — окремий процес, єдина відповідальність якого: оголосити
"для account_id настав новий день". Живе БЕЗ HTTP-сервера (щоб не
тягнути в контейнер, який 99.9% часу спить, цілий web-стек) — уся
взаємодія йде через Redis:

  * register()/unregister() — LPUSH команди в day_service:commands,
    fire-and-forget (той самий контракт, що був і з HTTP-версією:
    помилка реєстрації логується, але НЕ блокує add_account() у
    scheduler.py — day-service тимчасово недоступний це деградація,
    не збій).
  * get_status() — те саме LPUSH, але з reply_to; чекаємо відповідь
    через BLPOP з таймаутом (потрібно тільки для дебагу/адмінки, у
    звичайному робочому циклі не використовується).

Отримання самої події "новий день" — окремо, у src/core/day_events.py
(DayEventBus, pub/sub-слухач).
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from typing import Optional

import redis.asyncio as aioredis


class DayServiceError(Exception):
    """Загальна помилка day-service (Redis недоступний, таймаут відповіді)."""


@dataclass
class DaySchedule:
    account_id: str
    base_time: str
    jitter_minutes: int
    scheduled_time: str
    next_run_at: str
    last_day: Optional[str]


class DayServiceClient:
    def __init__(self, redis_url: Optional[str] = None) -> None:
        self._redis_url = redis_url or os.getenv("REDIS_URL", "redis://redis:6379/0")
        self._commands_key = os.getenv("DAY_SERVICE_COMMANDS_KEY", "day_service:commands")
        self._redis: Optional[aioredis.Redis] = None

    async def _client(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    async def aclose(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def register(
        self,
        account_id: str,
        base_time: Optional[str] = None,
        jitter_minutes: Optional[int] = None,
    ) -> None:
        """Ідемпотентна реєстрація акаунта в day-service. Fire-and-forget."""
        cmd: dict = {"action": "register", "account_id": account_id}
        if base_time is not None:
            cmd["base_time"] = base_time
        if jitter_minutes is not None:
            cmd["jitter_minutes"] = jitter_minutes
        client = await self._client()
        await client.lpush(self._commands_key, json.dumps(cmd))

    async def unregister(self, account_id: str) -> None:
        try:
            client = await self._client()
            await client.lpush(self._commands_key, json.dumps({"action": "unregister", "account_id": account_id}))
        except Exception:
            # day-service може бути тимчасово недоступний при видаленні
            # акаунта — це не має блокувати remove_account().
            pass

    async def get_status(self, account_id: str, timeout: float = 3.0) -> Optional[DaySchedule]:
        """Тільки для дебагу/адмінки — блокуючий запит-відповідь через Redis."""
        client = await self._client()
        reply_to = f"day_service:reply:{uuid.uuid4().hex}"
        cmd = {"action": "status", "account_id": account_id, "reply_to": reply_to}
        await client.lpush(self._commands_key, json.dumps(cmd))

        res = await client.blpop(reply_to, timeout=timeout)
        if res is None:
            raise DayServiceError("day-service не відповів вчасно")
        _, raw = res
        payload = json.loads(raw)
        if not payload.get("ok"):
            if payload.get("error") == "not_found":
                return None
            raise DayServiceError(f"day-service error: {payload}")
        return self._to_schedule(payload)

    @staticmethod
    def _to_schedule(payload: dict) -> DaySchedule:
        return DaySchedule(
            account_id=payload["account_id"],
            base_time=payload["base_time"],
            jitter_minutes=payload["jitter_minutes"],
            scheduled_time=payload["scheduled_time"],
            next_run_at=payload["next_run_at"],
            last_day=payload.get("last_day"),
        )


day_client = DayServiceClient()
