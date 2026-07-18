"""
queue_service/account_port/account_events.py — AccountEventBus.

Друга половина порту до account-service: "назначити калбек для socket".
account-service публікує кожну socket-подію акаунта в Redis-канал
`account_events:{account_id}` (готове рішення — Redis pub/sub замість
власного протоколу). Тут один фоновий psubscribe-слухач розкладає ці
повідомлення по callback'ах, зареєстрованих через subscribe().

Використання (SocketService):

    from queue_service.account_port.account_events import account_event_bus

    async def on_notify(payload: dict) -> None:
        ...

    account_event_bus.subscribe(account_id, "new-notify", on_notify)
    ...
    account_event_bus.unsubscribe(account_id, "new-notify", on_notify)
"""
from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict
from typing import Any, Awaitable, Callable

import redis.asyncio as aioredis

import logging

log = logging.getLogger("queue_service.account_events")

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]

_PREFIX = os.getenv("ACCOUNT_EVENTS_PREFIX", "account_events")


class AccountEventBus:
    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url or os.getenv("REDIS_URL", "redis://redis:6379/0")
        self._redis: aioredis.Redis | None = None
        self._pubsub: Any = None
        self._task: asyncio.Task[None] | None = None
        # (account_id, event) -> [callbacks]
        self._callbacks: dict[tuple[str, str], list[EventCallback]] = defaultdict(list)

    def subscribe(self, account_id: str, event: str, callback: EventCallback) -> None:
        self._callbacks[(account_id, event)].append(callback)

    def unsubscribe(self, account_id: str, event: str, callback: EventCallback | None = None) -> None:
        key = (account_id, event)
        if callback is None:
            self._callbacks.pop(key, None)
        else:
            self._callbacks[key] = [cb for cb in self._callbacks.get(key, []) if cb is not callback]

    def unsubscribe_account(self, account_id: str) -> None:
        for key in [k for k in self._callbacks if k[0] == account_id]:
            self._callbacks.pop(key, None)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        self._pubsub = self._redis.pubsub()
        await self._pubsub.psubscribe(f"{_PREFIX}:*")
        self._task = asyncio.create_task(self._run(), name="account-event-bus")
        log.info(f"[AccountEventBus] listening on {_PREFIX}:* ({self._redis_url})")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._pubsub is not None:
            await self._pubsub.close()
        if self._redis is not None:
            await self._redis.close()

    async def _run(self) -> None:
        assert self._pubsub is not None
        try:
            async for message in self._pubsub.listen():
                if message.get("type") != "pmessage":
                    continue
                try:
                    payload = json.loads(message["data"])
                except (json.JSONDecodeError, TypeError):
                    continue
                account_id = payload.get("account_id")
                event = payload.get("event")
                data = payload.get("data", {})
                if not account_id or not event:
                    continue
                for cb in list(self._callbacks.get((account_id, event), [])):
                    try:
                        await cb(data)
                    except Exception as e:
                        log.error(f"[AccountEventBus] callback [{account_id}/{event}] failed: {e}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error(f"[AccountEventBus] listener crashed: {e}", exc_info=True)


account_event_bus = AccountEventBus()
