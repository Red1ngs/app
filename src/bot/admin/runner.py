"""
bot/admin/runner.py — запуск адмін-бота в окремому daemon-потоці.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Optional

from aiogram import Bot, Dispatcher
from src.bot.admin.bot import create_admin_bot
from src.bot.admin.config import AdminBotConfig
from src.bot.services.scheduler_service import SchedulerService

from src.core.logging.loggers import get_logger
log = get_logger("admin.runner")


class AdminBotRunner:
    def __init__(self, config: AdminBotConfig, service: SchedulerService) -> None:
        self._config  = config
        self._service = service
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._dp: Optional[Dispatcher] = None
        self._bot: Optional[Bot] = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name="admin-bot",
            daemon=True,
        )
        self._thread.start()
        log.info("AdminBot started")

    def stop(self) -> None:
        """
        Викликається для чистого завершення роботи бота при Ctrl+C в main.py.
        """
        log.info("Stopping AdminBot polling...")
        if self._loop and self._loop.is_running():
            if self._dp:
                # Надійно зупиняємо polling в асинхронному циклі потоку бота
                future = asyncio.run_coroutine_threadsafe(self._dp.stop_polling(), self._loop)
                try:
                    future.result(timeout=4.0)  # Чекаємо на успішну зупинку
                except Exception as e:
                    log.warning(f"Error signaling AdminBot stop: {e}")

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        log.info("AdminBot stopped")

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._poll())
        except Exception as e:
            log.error(f"AdminBot error: {e}", exc_info=True)
        finally:
            self._loop.close()

    async def _poll(self) -> None:
        self._dp, self._bot = create_admin_bot(self._config, self._service)
        try:
            await self._dp.start_polling(self._bot, handle_signals=False)
        finally:
            # Тепер цей блок гарантовано виконається, коли dp.stop_polling() перерве цикл
            await self._bot.session.close()