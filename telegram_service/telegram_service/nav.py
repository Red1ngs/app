"""
telegram_service/nav.py — утиліта для single-message навігації.

Замість того щоб кожен callback робив answer() (новий меседж),
всі переходи редагують одне "головне" повідомлення бота.

Використання в роутерах:
    

    # Перший виклик (з Message) — створює повідомлення і зберігає id
    await nav_answer(message, state, text, reply_markup)

    # Всі наступні (з CallbackQuery) — редагують те саме повідомлення
    await nav_edit(call, state, text, reply_markup)

Якщо повідомлення з якоїсь причини не знайдено (видалено вручну) —
автоматично створюється нове і id оновлюється.
"""
from __future__ import annotations

from typing import Optional

from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
)

_NAV_KEY = "_nav_msg_id"


async def nav_answer(
    message:      Message,
    state:        FSMContext,
    text:         str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> Message:
    """
    Викликається з Message-хендлера (команда /accounts, /stats тощо).
    Відправляє нове повідомлення і зберігає його id у FSM state.
    """
    sent = await message.answer(text, reply_markup=reply_markup)
    await state.update_data({_NAV_KEY: sent.message_id})
    # Видаляємо повідомлення користувача щоб не смітити
    try:
        await message.delete()
    except Exception:
        pass
    return sent


async def nav_edit(
    call:         CallbackQuery,
    state:        FSMContext,
    text:         str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> None:
    """
    Викликається з CallbackQuery-хендлера.
    Редагує збережене навігаційне повідомлення.
    Якщо повідомлення не знайдено — створює нове.
    """
    data       = await state.get_data()
    nav_msg_id = data.get(_NAV_KEY)
    chat_id    = call.message.chat.id if call.message else None  # type: ignore[union-attr]

    if nav_msg_id and call.message and call.message.message_id == nav_msg_id:
        # Звичайний випадок — редагуємо поточне повідомлення
        try:
            await call.message.edit_text(text, reply_markup=reply_markup)
            return
        except Exception:
            pass  # якщо не вдалось — відправляємо нове

    # Fallback: відправляємо нове і зберігаємо id
    if call.message:
        try:
            sent = await call.message.answer(text, reply_markup=reply_markup)
            await state.update_data({_NAV_KEY: sent.message_id})
        except Exception:
            pass