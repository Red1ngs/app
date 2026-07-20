"""
day_service/main.py — точка входу.

Ніякого ASGI/HTTP: один asyncio-процес, який
  1) піднімає DayScheduler (єдиний heap-таймер на всі акаунти),
  2) у нескінченному циклі вичитує команди з Redis-списку і виконує їх.

Команди (dict з полем "action"):
  {"action": "register",   "account_id": ..., "base_time"?: ..., "jitter_minutes"?: ...}
  {"action": "unregister", "account_id": ...}
  {"action": "status",     "account_id": ..., "reply_to": "day_service:reply:..."}
  {"action": "list",       "reply_to": "day_service:reply:..."}
  {"action": "force",      "account_id": ..., "reply_to"?: "day_service:reply:..."}

"reply_to" опційний: register/unregister — fire-and-forget (клієнту не
потрібне підтвердження, як і раніше з HTTP-версією — помилка реєстрації
не мала блокувати add_account() у app). status/list завжди мають
reply_to (інакше немає сенсу їх викликати); force — опційний.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import time

from day_service import db
from day_service.config import settings
from day_service.redis_bus import CommandConsumer, DayEventPublisher
from day_service.scheduler import AccountSchedule, DayScheduler

log = logging.getLogger("day_service.main")

# Файл-"пульс": торкається його command-loop після кожної успішної спроби
# зв'язку з Redis (успішний BRPOP-виклик, з командою чи без). Дешевша й
# більш промовиста альтернатива HTTP-healthcheck: не додає ні порту, ні
# web-стеку, а healthcheck.py нижче лише перевіряє "свіжість" mtime.
_HEARTBEAT_PATH = os.getenv("DAY_SERVICE_HEARTBEAT_PATH", "/tmp/day_service_heartbeat")


def _touch_heartbeat() -> None:
    try:
        with open(_HEARTBEAT_PATH, "w"):
            pass
        os.utime(_HEARTBEAT_PATH, None)
    except OSError:
        pass


def _to_dict(sched: AccountSchedule) -> dict:
    return {
        "account_id": sched.account_id,
        "base_time": sched.base_time,
        "jitter_minutes": sched.jitter_minutes,
        "scheduled_time": sched.scheduled_time,
        "next_run_at": sched.next_run_at,
        "last_day": sched.last_day,
    }


async def _handle_command(cmd: dict, scheduler: DayScheduler, publisher: DayEventPublisher) -> None:
    action = cmd.get("action")
    account_id = cmd.get("account_id")
    reply_to = cmd.get("reply_to")

    if action == "register":
        if not account_id:
            return
        base_time = cmd.get("base_time") or settings.default_base_time
        jitter_minutes = cmd.get("jitter_minutes")
        jitter_minutes = settings.default_jitter_minutes if jitter_minutes is None else int(jitter_minutes)
        sched = scheduler.add_account(account_id, base_time, jitter_minutes)
        if reply_to:
            await publisher.reply(reply_to, {"ok": True, **_to_dict(sched)})
        return

    if action == "unregister":
        if account_id:
            existed = scheduler.remove_account(account_id)
        else:
            existed = False
        if reply_to:
            await publisher.reply(reply_to, {"ok": True, "existed": existed})
        return

    if action == "status":
        if not reply_to:
            return
        sched = scheduler.status(account_id) if account_id else None
        if sched is None:
            await publisher.reply(reply_to, {"ok": False, "error": "not_found"})
        else:
            await publisher.reply(reply_to, {"ok": True, **_to_dict(sched)})
        return

    if action == "list":
        if not reply_to:
            return
        await publisher.reply(reply_to, {"ok": True, "accounts": [_to_dict(s) for s in scheduler.all_status()]})
        return

    if action == "force":
        if not account_id:
            return
        published = await scheduler.force_trigger(account_id)
        if reply_to:
            if published is None:
                await publisher.reply(reply_to, {"ok": False, "error": "not_found"})
            else:
                await publisher.reply(reply_to, {"ok": True, "published": published})
        return

    log.warning(f"[main] невідома команда, пропуск: {cmd!r}")


async def _command_loop(scheduler: DayScheduler, consumer: CommandConsumer, publisher: DayEventPublisher, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            cmd = await consumer.next_command(timeout=5)
        except Exception as exc:
            # Redis тимчасово недоступний — не валимо весь процес, просто
            # не оновлюємо heartbeat (healthcheck за ~30с побачить "мовчання"
            # і докер сам перезапустить контейнер, якщо проблема не минеться).
            log.error(f"[main] Redis недоступний: {exc}")
            await asyncio.sleep(2)
            continue

        _touch_heartbeat()

        if cmd is None:
            continue
        try:
            await _handle_command(cmd, scheduler, publisher)
        except Exception as exc:
            log.error(f"[main] помилка обробки команди {cmd!r}: {exc}", exc_info=True)


async def run() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    conn = db.get_db(settings.db_path)
    publisher = DayEventPublisher()
    await publisher.start()

    scheduler = DayScheduler(conn, publisher)
    await scheduler.start()

    consumer = CommandConsumer()
    await consumer.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass  # Windows / деякі середовища без підтримки сигналів

    _touch_heartbeat()
    log.info("[main] day-service запущено (без HTTP, лише Redis)")
    command_task = asyncio.create_task(_command_loop(scheduler, consumer, publisher, stop))

    await stop.wait()

    log.info("[main] завершення роботи...")
    command_task.cancel()
    try:
        await command_task
    except (asyncio.CancelledError, Exception):
        pass

    await scheduler.stop()
    await consumer.stop()
    await publisher.stop()
    conn.close()


if __name__ == "__main__":
    asyncio.run(run())
