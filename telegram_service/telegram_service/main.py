"""
telegram_service/main.py — точка входу telegram-service.

На відміну від колишнього in-process admin-bot (окремий daemon-thread
усередині монолуту), тут бот — це весь процес: одна безпосередня
`dp.start_polling()` в основному asyncio loop'і, без threading. Уся
бізнес-логіка (акаунти/професії/статистика/логи) — за мережею, в
core-service; тут лишається лише Telegram UI-шар.
"""
from __future__ import annotations

import asyncio
import logging
import signal

try:
    from dotenv import load_dotenv
    load_dotenv(override=False)
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("telegram_service")

from telegram_service.bot import create_bot
from telegram_service.config import TelegramBotConfig
from telegram_service.core_client import CoreServiceClient


async def main() -> None:
    config = TelegramBotConfig.from_env()
    client = CoreServiceClient(config.core_service_url, config.core_service_token)
    dp, bot = create_bot(config, client)

    stop_event = asyncio.Event()

    def _request_stop() -> None:
        log.info("Shutdown requested...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            pass  # Windows dev-запуск — сигнали ігноруються, Ctrl+C все одно працює

    log.info(f"Starting polling (core-service: {config.core_service_url})")
    polling_task = asyncio.create_task(dp.start_polling(bot, handle_signals=False))

    await stop_event.wait()

    await dp.stop_polling()
    try:
        await asyncio.wait_for(polling_task, timeout=10.0)
    except asyncio.TimeoutError:
        polling_task.cancel()

    await bot.session.close()
    await client.aclose()
    log.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
