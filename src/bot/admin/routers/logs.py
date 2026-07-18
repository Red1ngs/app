"""
bot/admin/routers/logs.py

Команди для перегляду логів прямо в Telegram.

/logs                    — меню вибору типу логів
/logs account <id>       — останні 30 рядків акаунта
/logs errors             — помилки за останні 24 год
/logs scheduler          — останні 30 рядків scheduler.log
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.core.logging.reader import LogReader

router = Router(name="logs")
_reader = LogReader()

_MAX_MSG = 3800
_TAIL_N  = 40


# ── Helpers ───────────────────────────────────────────────────────────────────

def _split_text(text: str, chunk: int = _MAX_MSG) -> list[str]:
    return [text[i : i + chunk] for i in range(0, len(text), chunk)]


async def _send_lines(target: Message | CallbackQuery, lines: list[str], title: str) -> None:
    msg = target if isinstance(target, Message) else target.message

    if not lines:
        await msg.answer(f"📭 {title}\n\nЛог порожній або файл не знайдено")  # type: ignore[union-attr]
        return

    raw   = "\n".join(lines)
    parts = _split_text(f"📋 <b>{title}</b>\n\n<code>{raw}</code>")

    for part in parts:
        await msg.answer(part)  # type: ignore[union-attr]


# ── /logs — меню ──────────────────────────────────────────────────────────────

def _logs_menu_kb(account_ids: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="⚠️ Помилки (24 год)", callback_data="logs:errors")],
        [InlineKeyboardButton(text="🗓 Scheduler",         callback_data="logs:scheduler")],
    ]
    if account_ids:
        rows.append([InlineKeyboardButton(
            text="👤 Акаунт →", callback_data="logs:pick_account:account"
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _pick_account_kb(log_type: str, account_ids: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=acc_id,
            callback_data=f"logs:{log_type}:{acc_id}",
        )]
        for acc_id in account_ids
    ]
    rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data="logs:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("logs"))
async def cmd_logs(message: Message) -> None:
    account_ids = _reader.list_accounts()
    await message.answer(
        "📋 <b>Логи</b>\n\nОбери джерело:",
        reply_markup=_logs_menu_kb(account_ids),
    )


@router.callback_query(F.data == "logs:menu")
async def cb_logs_menu(call: CallbackQuery) -> None:
    account_ids = _reader.list_accounts()
    await call.message.edit_text(  # type: ignore[union-attr]
        "📋 <b>Логи</b>\n\nОбери джерело:",
        reply_markup=_logs_menu_kb(account_ids),
    )
    await call.answer()


# ── Вибір акаунта ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("logs:pick_account:"))
async def cb_pick_account(call: CallbackQuery) -> None:
    log_type    = call.data.split(":", 2)[2]  # type: ignore[union-attr]  → "account"
    account_ids = _reader.list_accounts()

    if not account_ids:
        await call.answer("📭 Лог-файлів акаунтів не знайдено", show_alert=True)
        return

    await call.message.edit_text(  # type: ignore[union-attr]
        "📋 <b>👤 Акаунт — оберіть акаунт:</b>",
        reply_markup=_pick_account_kb(log_type, account_ids),
    )
    await call.answer()


# ── Помилки ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "logs:errors")
async def cb_errors(call: CallbackQuery) -> None:
    await call.answer()
    lines = _reader.errors(since_hours=24)
    await _send_lines(call, lines, "Помилки за 24 год")


# ── Scheduler ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "logs:scheduler")
async def cb_scheduler(call: CallbackQuery) -> None:
    await call.answer()
    lines = _reader.tail_scheduler(_TAIL_N)
    await _send_lines(call, lines, f"Scheduler (останні {_TAIL_N} рядків)")


# ── Лог акаунта ───────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("logs:account:"))
async def cb_account_log(call: CallbackQuery) -> None:
    acc_id = call.data.split(":", 2)[2]  # type: ignore[union-attr]
    await call.answer()
    lines = _reader.tail_account(acc_id, _TAIL_N)
    await _send_lines(call, lines, f"Акаунт {acc_id} (останні {_TAIL_N} рядків)")


# ── Команда /logs з аргументами ───────────────────────────────────────────────

@router.message(Command("logs"))
async def cmd_logs_args(message: Message) -> None:
    """
    /logs account <id>  — лог акаунта
    /logs errors        — помилки 24год
    /logs scheduler     — scheduler лог
    """
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2:
        await cmd_logs(message)
        return

    sub = parts[1].lower()

    if sub == "errors":
        lines = _reader.errors(since_hours=24)
        await _send_lines(message, lines, "Помилки за 24 год")

    elif sub == "scheduler":
        lines = _reader.tail_scheduler(_TAIL_N)
        await _send_lines(message, lines, f"Scheduler (останні {_TAIL_N} рядків)")

    elif sub == "account" and len(parts) == 3:
        acc_id = parts[2].strip()
        lines = _reader.tail_account(acc_id, _TAIL_N)
        await _send_lines(message, lines, f"Акаунт {acc_id} (останні {_TAIL_N} рядків)")

    else:
        await message.answer(
            "Використання:\n"
            "/logs\n"
            "/logs errors\n"
            "/logs scheduler\n"
            "/logs account &lt;id&gt;"
        )