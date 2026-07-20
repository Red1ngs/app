"""
accounts/loader_tools.py
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from telegram_service.core_client import CoreServiceClient
from .profession_menu import profession_menu_registry

router = Router(name="accounts:loader_tools")


# ── Реєстрація пунктів меню ───────────────────────────────────────────────────

profession_menu_registry.register(
    profession_id="catalog_loader",
    label="🔄 Скинути сторінку каталогу",
    callback_template="acc:reset_catalog_page:{acc_id}",
)


# ── Reset catalog page ────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("acc:reset_catalog_page:"))
async def cb_reset_catalog_page(
    call: CallbackQuery,
    svc: CoreServiceClient,
) -> None:
    acc_id = call.data.split(":", 2)[2]
    info   = await svc.account_info(acc_id)
    if info is None:
        await call.answer("❌ Акаунт не знайдено", show_alert=True)
        return

    if "catalog_loader" not in info.professions:
        await call.answer("❌ Доступно тільки для catalog_loader", show_alert=True)
        return

    ok, err = await svc.reset_catalog_page(acc_id)
    if ok:
        await call.answer("✅ Сторінку каталогу скинуто на 1", show_alert=True)
    else:
        await call.answer(f"❌ {err}", show_alert=True)