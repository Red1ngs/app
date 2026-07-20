"""
accounts/profession_tools.py

Окреме вікно "🛠 Налаштування професій".

Раніше усі пункти-інструменти активних професій (наприклад "✅ Прочитані
глави" для reader, "📦 Слоти" для loader-подібних професій) додавались
прямо в головне меню акаунта одним довгим списком. Тепер вони згруповані
по професіях і відкриваються окремим екраном:

    Меню акаунта
        └─ 🛠 Налаштування професій
              ├─ 🎓 reader        (окреме вікно з пунктами reader)
              ├─ 🎓 loader        (окреме вікно з пунктами loader)
              └─ ...

Якщо активна лише одна професія з інструментами — відкриваємо її вікно
одразу, без проміжного вибору.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from telegram_service.core_client import CoreServiceClient
from .profession_menu import profession_menu_registry

router = Router(name="accounts:profession_tools")


# ── Клавіатури ────────────────────────────────────────────────────────────────

def _professions_kb(acc_id: str, professions: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"🎓 {pid}", callback_data=f"acc:prof_tools:{acc_id}:{pid}")]
        for pid in professions
    ]
    rows.append([InlineKeyboardButton(text="↩️ До меню акаунта", callback_data=f"acc:menu:{acc_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _tools_kb(acc_id: str, profession_id: str, back_multi: bool) -> InlineKeyboardMarkup:
    items = profession_menu_registry.items_for_profession(profession_id)
    rows = [
        [InlineKeyboardButton(text=item.label, callback_data=item.build_callback(acc_id))]
        for item in items
    ]
    back_cb = f"acc:prof_tools:{acc_id}" if back_multi else f"acc:menu:{acc_id}"
    back_label = "↩️ Назад" if back_multi else "↩️ До меню акаунта"
    rows.append([InlineKeyboardButton(text=back_label, callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Хендлер ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("acc:prof_tools:"))
async def cb_prof_tools(call: CallbackQuery, svc: CoreServiceClient) -> None:
    parts = call.data.split(":", 3)  # type: ignore[union-attr]
    acc_id = parts[2]
    profession_id = parts[3] if len(parts) > 3 else None

    info = await svc.account_info(acc_id)
    if info is None:
        await call.answer("❌ Акаунт не знайдено", show_alert=True)
        return

    profs_with_tools = profession_menu_registry.professions_with_items(list(info.professions))
    if not profs_with_tools:
        await call.answer("📭 Для активних професій немає налаштувань", show_alert=True)
        return

    multi = len(profs_with_tools) > 1

    if profession_id is None:
        if not multi:
            profession_id = profs_with_tools[0]
        else:
            await call.message.edit_text(  # type: ignore[union-attr]
                f"🛠 <b>Налаштування професій</b>\n"
                f"Акаунт <code>{acc_id}</code>\n\n"
                "Обери професію:",
                reply_markup=_professions_kb(acc_id, profs_with_tools),
            )
            await call.answer()
            return

    if profession_id not in profs_with_tools:
        await call.answer("❌ Ця професія неактивна", show_alert=True)
        return

    await call.message.edit_text(  # type: ignore[union-attr]
        f"🛠 <b>{profession_id}</b>\n"
        f"Акаунт <code>{acc_id}</code>\n\n"
        "Обери дію:",
        reply_markup=_tools_kb(acc_id, profession_id, back_multi=multi),
    )
    await call.answer()
