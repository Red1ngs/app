"""
telegram_service/watchdog.py — ProblemAccountsNotifier.

core-service (ReconnectWatchdog) сам намагається відновити акаунти, що
впали в ERROR — але адмін про це раніше дізнавався лише випадково,
відкривши /accounts, або вручну читаючи логи. Цей модуль — фоновий таск
(в тому ж процесі, що й polling бота), який раз на `poll_interval` секунд
запитує core-service (`problem_accounts` RPC) і сам пише адмінам у Telegram:

  - ⛔ DEAD — одразу (акаунт забанений чи інша постійна проблема,
    ReconnectWatchdog його свідомо не чіпає — потрібне ручне втручання).
  - ⚠️ ERROR — тільки якщо стан тримається довше `error_alert_after`
    секунд (щоб не спамити на кожну транзитну мережеву гикавку, яку
    ReconnectWatchdog і так підхопить сам за перші один-два цикли).
  - ✅ resolved — коли акаунт зникає зі списку проблемних (сам чи
    ReconnectWatchdog його підняв).

Алерт на кожен обліковий запис шлеться один раз (доки не резолвиться),
а не на кожен цикл опитування — це найважливіше для не-спамної поведінки.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from aiogram import Bot

from telegram_service.core_client import CoreServiceClient, CoreServiceError

log = logging.getLogger("telegram_service.watchdog")


class ProblemAccountsNotifier:
    def __init__(
        self,
        client:            CoreServiceClient,
        bot:               Bot,
        admin_ids:         set[int],
        poll_interval:     float = 90.0,
        error_alert_after: float = 300.0,
    ) -> None:
        self._client = client
        self._bot = bot
        self._admin_ids = admin_ids
        self._poll_interval = poll_interval
        self._error_alert_after = error_alert_after

        self._first_seen: dict[str, float] = {}   # account_id -> monotonic() коли вперше побачили ERROR
        self._alerted:    set[str]         = set()  # account_id, про які вже сповістили (і ще не resolved)

        self._task: Optional[asyncio.Task[None]] = None
        self._stopping = False

    def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="problem-accounts-notifier")
        log.info(
            f"[ProblemAccountsNotifier] запущено "
            f"(poll_interval={self._poll_interval}s, error_alert_after={self._error_alert_after}s)"
        )

    async def stop(self) -> None:
        self._stopping = True
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
        log.info("[ProblemAccountsNotifier] зупинено")

    async def _run(self) -> None:
        while not self._stopping:
            try:
                await asyncio.sleep(self._poll_interval)
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error(f"[ProblemAccountsNotifier] помилка в циклі: {e}", exc_info=True)

    async def _tick(self) -> None:
        try:
            problems = await self._client.problem_accounts()
        except CoreServiceError as e:
            # core-service тимчасово недоступний — не варто ні алертити,
            # ні губити накопичений стан; спробуємо знову наступного тіку.
            log.warning(f"[ProblemAccountsNotifier] core-service недоступний: {e}")
            return

        now = time.monotonic()
        current_ids: set[str] = set()

        for p in problems:
            current_ids.add(p.account_id)

            if p.status == "DEAD":
                if p.account_id not in self._alerted:
                    await self._notify(
                        f"⛔ Акаунт <b>{p.account_id}</b> в статусі DEAD: {p.error or '—'}\n"
                        f"Потрібне ручне втручання (бан/невірний пароль) — "
                        f"автовідновлення цей акаунт свідомо не чіпає."
                    )
                    self._alerted.add(p.account_id)
                continue

            # status == "ERROR" — даємо ReconnectWatchdog-у шанс полагодити
            # самому, алертимо лише якщо це затягнулось.
            first_seen = self._first_seen.setdefault(p.account_id, now)
            if now - first_seen >= self._error_alert_after and p.account_id not in self._alerted:
                minutes = int((now - first_seen) // 60)
                await self._notify(
                    f"⚠️ Акаунт <b>{p.account_id}</b> в статусі ERROR понад {minutes} хв: "
                    f"{p.error or '—'}\nАвтовідновлення (ReconnectWatchdog) продовжує пробувати."
                )
                self._alerted.add(p.account_id)

        # Усе, що зникло зі списку проблемних — відновилось (само чи через
        # ReconnectWatchdog/ручний /reconnect) — прибираємо стан і, якщо
        # раніше алертили, повідомляємо про це.
        resolved = (set(self._first_seen) | self._alerted) - current_ids
        for account_id in resolved:
            self._first_seen.pop(account_id, None)
            was_alerted = account_id in self._alerted
            self._alerted.discard(account_id)
            if was_alerted:
                await self._notify(f"✅ Акаунт <b>{account_id}</b> відновлено")

    async def _notify(self, text: str) -> None:
        for admin_id in self._admin_ids:
            try:
                await self._bot.send_message(admin_id, text)
            except Exception as e:
                log.warning(f"[ProblemAccountsNotifier] не вдалось надіслати адміну {admin_id}: {e}")
