"""
day_service/redis_bus.py — вся взаємодія сервісу з Redis.

Навмисно без web-фреймворку: day-service не слухає жодного порту.
Замість цього:

  * ВХІД:  список-черга day_service:commands (BRPOP) — команди
           register/unregister/status/list/force від app. LIST, а не
           pub/sub, бо команди не можна губити (реєстрація акаунта),
           тоді як для новин про "новий день" (нижче) втрата
           повідомлення не критична — DailyMonitor сам підстраховується.

  * ВИХІД: pub/sub-канал day_service_events:{account_id} — подія
           "новий день настав". Формат ІДЕНТИЧНИЙ до того, як
           account-service публікує socket-події (account_events:{id}),
           щоб на боці app можна було перевикористати вже наявний
           патерн слухача (AccountEventBus/DayEventBus), а не
           винаходити новий протокол.
"""
from __future__ import annotations

import json
import logging
import uuid

import redis.asyncio as aioredis

from day_service.config import settings

log = logging.getLogger("day_service.redis_bus")

# Скільки тримати ключ з відповіддю на команду, якщо клієнт чомусь не
# забрав його (BLPOP з таймаутом на боці клієнта) — щоб не текла пам'ять
# Redis від "забутих" відповідей.
_REPLY_TTL_S = 30


class DayEventPublisher:
    """Публікація "новий день настав" (pub/sub, fire-and-forget)."""

    def __init__(self, redis_url: str | None = None, prefix: str | None = None) -> None:
        self._redis_url = redis_url or settings.redis_url
        self._prefix = prefix or settings.events_prefix
        self._redis: aioredis.Redis | None = None

    async def start(self) -> None:
        self._redis = aioredis.from_url(
            self._redis_url,
            decode_responses=True,
            socket_timeout=None,       # не рвати з'єднання під час блокуючого BRPOP
            socket_connect_timeout=5,  # а на сам коннект — таймаут лишити
        )

    async def stop(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def publish_new_day(self, account_id: str, day: str) -> None:
        if self._redis is None:
            await self.start()
        channel = f"{self._prefix}:{account_id}"
        payload = json.dumps({
            "account_id": account_id,
            "event": "new_day",
            "data": {"day": day},
        })
        await self._redis.publish(channel, payload)
        log.info(f"[{account_id}] published new_day (day={day}) → {channel}")

    async def reply(self, reply_to: str, payload: dict) -> None:
        if self._redis is None:
            await self.start()
        await self._redis.lpush(reply_to, json.dumps(payload))
        await self._redis.expire(reply_to, _REPLY_TTL_S)


class CommandConsumer:
    """
    Слухає day_service:commands (BRPOP — блокуюче очікування, нуль CPU
    між командами) і віддає готові dict-и виклику коду вище.
    """

    def __init__(self, redis_url: str | None = None, key: str | None = None) -> None:
        self._redis_url = redis_url or settings.redis_url
        self._key = key or settings.commands_key
        self._redis: aioredis.Redis | None = None

    async def start(self) -> None:
        self._redis = aioredis.from_url(
            self._redis_url,
            decode_responses=True,
            socket_timeout=None,       # не рвати з'єднання під час блокуючого BRPOP
            socket_connect_timeout=5,  # а на сам коннект — таймаут лишити
        )

    async def stop(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def next_command(self, timeout: int = 5) -> dict | None:
        """
        BRPOP з таймаутом (а не 0/нескінченність), щоб цикл періодично
        прокидався і міг коректно завершитись при cancel() — infinite
        BRPOP не реагує на asyncio-скасування, доки не прийде команда.
        """
        assert self._redis is not None
        res = await self._redis.brpop(self._key, timeout=timeout)
        if res is None:
            return None
        _, raw = res
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            log.warning(f"[CommandConsumer] невалідна команда, пропуск: {raw!r}")
            return None


def new_correlation_key() -> str:
    return f"day_service:reply:{uuid.uuid4().hex}"
