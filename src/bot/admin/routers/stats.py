"""
bot/admin/routers/stats.py

/stats        — загальна статистика
/stats <id>   — детально по акаунту
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

from src.bot.admin.nav import nav_answer
from src.bot.services.scheduler_service import AccountInfo, SchedulerService

router = Router(name="stats")


def _account_stats_text(info: AccountInfo) -> str:
    proxy = f"\nПроксі: <code>{info.proxy}</code>" if info.proxy else ""

    prof_line = (
        "Професії: <b>" + ", ".join(
            p for p in info.professions
        ) + "</b>"
        if info.professions else
        "Професії: <i>не призначено</i>"
    )

    monitor_line = (
        "Монітори: <b>" + ", ".join(
            m for m in info.monitors
        ) + "</b>"
        if info.monitors else
        "Монітори: <i>немає</i>"
    )

    return (
        f"📊 <b>Статистика: {info.account_id}</b>\n\n"
        f"Email:  <code>{info.email}</code>{proxy}\n"
        f"Статус: <b>{info.status}</b>\n"
        f"{prof_line}\n"
        f"{monitor_line}"
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message, state: FSMContext, svc: SchedulerService) -> None:
    parts  = (message.text or "").split(maxsplit=1)
    acc_id = parts[1].strip() if len(parts) > 1 else None

    if acc_id:
        info = await svc.account_info(acc_id)
        if info is None:
            await nav_answer(message, state, f"❌ Акаунт <code>{acc_id}</code> не знайдено")
            return
        await nav_answer(
            message, state,
            _account_stats_text(info),
            InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="↩️ До списку", callback_data="acc:list"),
            ]]),
        )
        return

    snapshot    = await svc.snapshot()
    status_count: dict[str, int] = {}
    for acc in snapshot.accounts:
        status_count[acc.status] = status_count.get(acc.status, 0) + 1

    lines = [f"📊 <b>Загальна статистика</b>\n"]
    lines.append(f"Акаунтів: <b>{snapshot.total_accounts}</b>\n")
    lines.append("<b>За статусами:</b>")
    for status, count in sorted(status_count.items()):
        lines.append(f"  {status}: {count}")
        
    if snapshot.accounts:
        lines.append("\n<b>Деталі:</b>")
        for acc in snapshot.accounts:
            monitors_str = ", ".join(acc.monitors) if acc.monitors else "—"
            lines.append(
                f"  <code>{acc.account_id}</code> [{acc.status}] "
                f"monitors={monitors_str}"
            )

    await nav_answer(
        message, state,
        "\n".join(lines),
        InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📋 Акаунти", callback_data="acc:list"),
        ]]),
    )


@router.callback_query(F.data.startswith("stats:acc:"))
async def cb_stats_account(call: CallbackQuery, svc: SchedulerService) -> None:
    acc_id = call.data.split(":", 2)[2]  # type: ignore[union-attr]
    info   = await svc.account_info(acc_id)
    if info is None:
        await call.answer("❌ Акаунт не знайдено", show_alert=True)
        return
    await call.message.edit_text(  # type: ignore[union-attr]
        _account_stats_text(info),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="↩️ До меню", callback_data=f"acc:menu:{acc_id}"),
        ]]),
    )
    await call.answer()