"""
accounts/list_.py

/accounts — команда і callback acc:list.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from telegram_service.nav import nav_answer
from telegram_service.core_client import CoreServiceClient
from ._common import accounts_list_kb

router = Router(name="accounts:list")

_EMPTY_KB = InlineKeyboardMarkup(inline_keyboard=[[
    InlineKeyboardButton(text="➕ Додати акаунт", callback_data="acc:add"),
]])

_NAV_ROW = [
    InlineKeyboardButton(text="🔎 Пошук", callback_data="acc:search:menu"),
    InlineKeyboardButton(text="🗂 Категорії", callback_data="acc:group:menu"),
]


@router.message(Command("accounts"))
async def cmd_accounts(message: Message, state: FSMContext, svc: CoreServiceClient) -> None:
    snapshot = await svc.snapshot()
    if not snapshot.accounts:
        await nav_answer(message, state, "📭 Акаунтів ще немає.\n\nДодай перший:", _EMPTY_KB)
    else:
        await nav_answer(
            message, state,
            f"<b>Акаунти ({snapshot.total_accounts})</b>\nОбери акаунт або додай новий:",
            accounts_list_kb(snapshot.accounts, extra_rows=[_NAV_ROW]),
        )


@router.callback_query(F.data == "acc:list")
async def cb_list(call: CallbackQuery, svc: CoreServiceClient) -> None:
    snapshot = await svc.snapshot()
    if not snapshot.accounts:
        await call.message.edit_text("📭 Акаунтів немає.", reply_markup=_EMPTY_KB)  # type: ignore[union-attr]
    else:
        await call.message.edit_text(  # type: ignore[union-attr]
            f"<b>Акаунти ({snapshot.total_accounts})</b>\nОбери акаунт або додай новий:",
            reply_markup=accounts_list_kb(snapshot.accounts, extra_rows=[_NAV_ROW]),
        )
    await call.answer()