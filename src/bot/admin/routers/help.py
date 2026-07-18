"""
bot/admin/routers/help.py — /start і /help
"""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.bot.admin.nav import nav_answer

router = Router(name="help")

HELP_TEXT = """
🤖 <b>Mangabuff Admin Bot</b>

<b>Акаунти</b>
/accounts — список акаунтів (кнопки з кольоровим статусом)
  🔎 Пошук — за ID, email, професією, статусом, підключенням, проксі
  🗂 Категорії — групування списку (за статусом / професією / підключенням)

<b>Статистика</b>
/stats          — загальна статистика
/stats &lt;id&gt;     — статистика конкретного акаунта

<b>Логи</b>
/logs                    — меню логів
/logs errors             — помилки за 24 год
/logs scheduler          — scheduler лог
/logs account &lt;id&gt;      — лог акаунта

<b>З меню акаунта можна:</b>
  ⏸ Призупинити / ▶️ Відновити
  🎓 Керувати професіями (додати / прибрати, з пошуком)
  🛠 Відкрити налаштування конкретної активної професії (окреме вікно)
  🗑 Видалити акаунт
  ➕ Додати новий акаунт
""".strip()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await nav_answer(
        message, state, HELP_TEXT,
        InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📋 Відкрити акаунти", callback_data="acc:list"),
        ]]),
    )


@router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext) -> None:
    await nav_answer(
        message, state, HELP_TEXT,
        InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📋 Відкрити акаунти", callback_data="acc:list"),
        ]]),
    )