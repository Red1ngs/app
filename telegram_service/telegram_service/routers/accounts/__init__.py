"""
accounts/__init__.py

Збирає всі sub-роутери пакету в один accounts router.
Зовні (bot.py) підключається як раніше:

    from telegram_service.routers import accounts
    dp.include_router(accounts.router)
"""
from aiogram import Router

from telegram_service.routers.accounts import (
    add, list_, menu, profession, profession_tools, reader_tools, loader_tools, slots, search,
)

router = Router(name="accounts")
router.include_router(list_.router)
router.include_router(search.router)
router.include_router(menu.router)
router.include_router(profession.router)
router.include_router(profession_tools.router)
router.include_router(reader_tools.router)
router.include_router(loader_tools.router)
router.include_router(slots.router)
router.include_router(add.router)