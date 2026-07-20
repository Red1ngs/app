"""
telegram_service/middlewares.py
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from telegram_service.config import TelegramBotConfig
from telegram_service.core_client import CoreServiceClient


class AuthMiddleware(BaseMiddleware):
    def __init__(self, config: TelegramBotConfig) -> None:
        self._ids = config.admin_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event:   TelegramObject,
        data:    dict[str, Any],
    ) -> Any:
        uid: int | None = None
        if isinstance(event, Message) and event.from_user:
            uid = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            uid = event.from_user.id

        if uid is None or uid not in self._ids:
            if isinstance(event, Message):
                await event.answer("⛔ Access denied")
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Access denied", show_alert=True)
            return
        return await handler(event, data)


class ServiceMiddleware(BaseMiddleware):
    """Ін'єктує CoreServiceClient у data['svc']."""

    def __init__(self, client: CoreServiceClient) -> None:
        self._svc = client

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event:   TelegramObject,
        data:    dict[str, Any],
    ) -> Any:
        data["svc"] = self._svc
        return await handler(event, data)
