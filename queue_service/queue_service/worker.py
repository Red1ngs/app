"""
queue_service/worker.py

Приклад того, як новий сервіс користується ТИМ САМИМ портом до
account-service, що й app — на тих самих акаунтах, просто інша
активність (тут — умовна "черга задач").

Два патерни з ТЗ:
  1. "Зроби запит"       → account_client.request(...)
  2. "Калбек для socket" → account_event_bus.subscribe(...)

Замінити action_handlers на реальну бізнес-логіку цього сервісу.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any

import redis.asyncio as aioredis

from queue_service.account_port.account_client import account_client
from queue_service.account_port.account_events import account_event_bus
from queue_service.config import settings
from queue_service.db import get_db

import logging

log = logging.getLogger("queue_service.worker")


# ── Патерн 1: обробка задач із власної черги (Redis list) ───────────────────
#
# Продюсер (окремий процес / API-роут / інший сервіс) кладе задачу так:
#   await redis.lpush("queue_service:tasks", json.dumps({
#       "account_id": "acc1", "action": "ping_profile",
#   }))

async def handle_ping_profile(conn: sqlite3.Connection, account_id: str, task: dict[str, Any]) -> None:
    """Приклад: зайти на сторінку профілю через ТОЙ САМИЙ акаунт, яким
    користується app, — просто інший ендпоінт/інша мета."""
    try:
        r = await account_client.request(account_id, "GET", "/", room="/", priority="BACKGROUND")
        status = "ok" if r.status_code == 200 else f"http_{r.status_code}"
    except Exception as e:
        status = "error"
        log.error(f"[{account_id}] ping_profile failed: {e}")

    conn.execute(
        "INSERT INTO task_log (account_id, action, status, detail) VALUES (?, ?, ?, ?)",
        (account_id, "ping_profile", status, json.dumps(task, ensure_ascii=False)),
    )
    conn.commit()


ACTION_HANDLERS = {
    "ping_profile": handle_ping_profile,
}


async def task_loop(conn: sqlite3.Connection) -> None:
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    log.info(f"[worker] слухаю чергу {settings.task_queue_key!r}")
    while True:
        item = await redis.brpop(settings.task_queue_key, timeout=5)
        if item is None:
            continue
        _, raw = item
        try:
            task = json.loads(raw)
        except json.JSONDecodeError:
            log.warning(f"[worker] некоректна задача, пропущено: {raw!r}")
            continue

        account_id = task.get("account_id")
        action = task.get("action")
        handler = ACTION_HANDLERS.get(action)
        if not account_id or handler is None:
            log.warning(f"[worker] невідома задача: {task!r}")
            continue

        await handler(conn, account_id, task)


# ── Патерн 2: реакція на socket-подію конкретного акаунта ────────────────────
#
# Ідентичний механізм, яким користується SocketService у app: та сама
# Redis-подія (account-service публікує лише раз, незалежно від того,
# скільки сервісів на неї підписані).

def subscribe_account_to_events(account_id: str) -> None:
    async def on_new_notify(data: dict[str, Any]) -> None:
        log.info(f"[{account_id}] отримано new-notify через account_event_bus: {data}")
        # тут — власна бізнес-логіка сервісу (напр. покласти задачу в чергу)

    account_event_bus.subscribe(account_id, "new-notify", on_new_notify)


async def run() -> None:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    conn = get_db(settings.db_path)

    await account_event_bus.start()

    try:
        await task_loop(conn)
    finally:
        await account_event_bus.stop()
        await account_client.aclose()
