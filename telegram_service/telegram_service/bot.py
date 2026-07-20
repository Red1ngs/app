"""
telegram_service/bot.py — фабрика Dispatcher + Bot.

Порядок middleware:
    1. ServiceMiddleware  — ін'єктує CoreServiceClient у data['svc']
    2. AuthMiddleware     — відсіває не-адмінів

FSM storage: MemoryStorage (якщо потрібна персистентність між рестартами —
замінити на RedisStorage; Redis тут вже й так є в мережі).
"""
from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from telegram_service.config import TelegramBotConfig
from telegram_service.core_client import CoreServiceClient
from telegram_service.middlewares import AuthMiddleware, ServiceMiddleware
from telegram_service.routers import accounts, help, logs, stats

COMMANDS = [
    BotCommand(command="accounts", description="📋 Список акаунтів"),
    BotCommand(command="stats",    description="📊 Статистика"),
    BotCommand(command="logs",     description="🗂 Логи"),
    BotCommand(command="help",     description="❓ Допомога"),
]


async def _set_commands(bot: Bot) -> None:
    await bot.set_my_commands(COMMANDS)


def create_bot(config: TelegramBotConfig, client: CoreServiceClient) -> tuple[Dispatcher, Bot]:
    bot = Bot(
        token=config.token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # Middleware (order matters: inject first, then auth)
    for update_type in (dp.message, dp.callback_query):
        update_type.middleware(ServiceMiddleware(client))
        update_type.middleware(AuthMiddleware(config))

    # Роутери
    dp.include_router(help.router)
    dp.include_router(accounts.router)
    dp.include_router(stats.router)
    dp.include_router(logs.router)

    # Команди у меню клавіатури (реєструються при старті polling)
    dp.startup.register(_set_commands)

    return dp, bot
