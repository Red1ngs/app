"""
accounts/menu.py
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram_service.core_client import CoreServiceClient
from ._common import account_text, account_menu_kb, confirm_remove_kb

router = Router(name="accounts:menu")


async def _redraw(call: CallbackQuery, svc: CoreServiceClient, acc_id: str) -> None:
    info = await svc.account_info(acc_id)
    if info:
        await call.message.edit_text(  # type: ignore[union-attr]
            account_text(info),
            reply_markup=account_menu_kb(acc_id, info.status, list(info.professions), info.is_connected),
        )


# ── Меню ──────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("acc:menu:"))
async def cb_menu(call: CallbackQuery, svc: CoreServiceClient) -> None:
    acc_id = call.data.split(":", 2)[2]
    info   = await svc.account_info(acc_id)
    if info is None:
        await call.answer("❌ Акаунт не знайдено", show_alert=True)
        return
    await call.message.edit_text(  # type: ignore[union-attr]
        account_text(info),
        reply_markup=account_menu_kb(acc_id, info.status, list(info.professions), info.is_connected),
    )
    await call.answer()


@router.callback_query(F.data.startswith("acc:refresh:"))
async def cb_refresh(call: CallbackQuery, svc: CoreServiceClient) -> None:
    acc_id = call.data.split(":", 2)[2]
    info   = await svc.account_info(acc_id)
    if info is None:
        await call.answer("❌ Акаунт не знайдено", show_alert=True)
        return
    await call.message.edit_text(  # type: ignore[union-attr]
        account_text(info),
        reply_markup=account_menu_kb(acc_id, info.status, list(info.professions), info.is_connected),
    )
    await call.answer("🔄 Оновлено")


# ── Пауза / відновлення ───────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("acc:pause:"))
async def cb_pause(call: CallbackQuery, svc: CoreServiceClient) -> None:
    acc_id = call.data.split(":", 2)[2]
    ok     = await svc.pause(acc_id)
    await call.answer("⏸ Призупинено" if ok else "❌ Не вдалось", show_alert=not ok)
    if ok:
        await _redraw(call, svc, acc_id)


@router.callback_query(F.data.startswith("acc:resume:"))
async def cb_resume(call: CallbackQuery, svc: CoreServiceClient) -> None:
    acc_id = call.data.split(":", 2)[2]
    ok     = await svc.resume(acc_id)
    await call.answer("▶️ Відновлено" if ok else "❌ Не вдалось", show_alert=not ok)
    if ok:
        await _redraw(call, svc, acc_id)


# ── Підключення / відключення сесії ────────────────────────────────────────────

@router.callback_query(F.data.startswith("acc:connect:"))
async def cb_connect(call: CallbackQuery, svc: CoreServiceClient) -> None:
    acc_id = call.data.split(":", 2)[2]
    await call.answer("⏳ Підключаємо…")
    ok = await svc.connect_account(acc_id)
    if not ok:
        err = await svc.get_account_error(acc_id) or "невідома помилка"
        await call.message.answer(  # type: ignore[union-attr]
            f"❌ Не вдалось підключити <code>{acc_id}</code>:\n{err}",
            parse_mode="HTML",
        )
    await _redraw(call, svc, acc_id)


@router.callback_query(F.data.startswith("acc:disconnect:"))
async def cb_disconnect(call: CallbackQuery, svc: CoreServiceClient) -> None:
    acc_id = call.data.split(":", 2)[2]
    await svc.disconnect_account(acc_id)
    await call.answer("🔌 Відключено")
    await _redraw(call, svc, acc_id)


# ── Видалення ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("acc:remove:"))
async def cb_remove(call: CallbackQuery, svc: CoreServiceClient) -> None:
    acc_id = call.data.split(":", 2)[2]
    await call.message.edit_text(  # type: ignore[union-attr]
        f"🗑 Видалити акаунт <code>{acc_id}</code>?\nЦю дію не можна скасувати.",
        reply_markup=confirm_remove_kb(acc_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("acc:remove_confirm:"))
async def cb_remove_confirm(call: CallbackQuery, svc: CoreServiceClient) -> None:
    acc_id = call.data.split(":", 2)[2]
    if not await svc.remove(acc_id):
        await call.answer("❌ Не вдалось видалити", show_alert=True)
        return
    await call.message.edit_text(  # type: ignore[union-attr]
        f"✅ Акаунт <code>{acc_id}</code> видалено.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="↩️ До списку", callback_data="acc:list"),
        ]]),
    )
    await call.answer()