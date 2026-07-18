"""
accounts/reader_tools.py
"""
from __future__ import annotations
import html

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from src.bot.services.scheduler_service import SchedulerService
from ._common import cancel_add_kb
from .profession_menu import profession_menu_registry

router = Router(name="accounts:reader_tools")


# ── Реєстрація пунктів меню ───────────────────────────────────────────────────

profession_menu_registry.register(
    profession_id="manga_loader",
    label="🔍 Парсинг манг",
    callback_template="acc:force_parse:{acc_id}",
)

profession_menu_registry.register(
    profession_id="reader",
    label="✅ Прочитані глави",
    callback_template="acc:mark_read:{acc_id}",
)


# ── FSM ───────────────────────────────────────────────────────────────────────

class ForceParseFSM(StatesGroup):
    wait_input = State()


class MarkReadFSM(StatesGroup):
    wait_input = State()


# ── Force parse ───────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("acc:force_parse:"))
async def cb_force_parse_start(
    call: CallbackQuery,
    state: FSMContext,
    svc: SchedulerService,
) -> None:
    acc_id = call.data.split(":", 2)[2]
    info   = await svc.account_info(acc_id)
    if info is None:
        await call.answer("❌ Акаунт не знайдено", show_alert=True)
        return

    if "manga_loader" not in info.professions:
        await call.answer("❌ Доступно тільки для manga_loader", show_alert=True)
        return

    await state.set_state(ForceParseFSM.wait_input)
    await state.update_data(acc_id=acc_id, _nav_msg_id=call.message.message_id)

    await call.message.edit_text(
        f"🔍 <b>Примусовий парсинг манг</b> для <code>{acc_id}</code>\n\n"
        "Введіть <b>список translit_name через кому</b>:\n\n"
        "<i>Приклад:</i>\n"
        "• <code>vsevedushchii-chitatel, naruto, one-piece</code>",
        reply_markup=cancel_add_kb(),
    )
    await call.answer()


@router.message(ForceParseFSM.wait_input)
async def fsm_force_parse_input(
    message: Message,
    state: FSMContext,
    svc: SchedulerService,
) -> None:
    text = (message.text or "").strip()
    data = await state.get_data()
    acc_id: str = data.get("acc_id", "")
    await state.clear()

    targets = [t.strip() for t in text.split(",") if t.strip()]
    if not targets:
        await message.answer("❌ Введіть хоча б один translit_name. Операцію скасовано.")
        return

    await message.answer(f"⏳ Запускаю парсинг: {', '.join(targets)}…")

    ok, reason, result = await svc.force_parse_mangas(acc_id, targets=targets)

    if ok:
        await message.answer(
            f"✅ <b>Парсинг завершено</b>\n"
            f"Збережено: <b>{result.get('mangas', 0)}</b> манг, "
            f"<b>{result.get('chapters', 0)}</b> глав."
        )
    else:
        await message.answer(f"❌ Помилка парсингу:\n<code>{reason}</code>")


# ── Mark read ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("acc:mark_read:"))
async def cb_mark_read_start(
    call: CallbackQuery,
    state: FSMContext,
    svc: SchedulerService,
) -> None:
    acc_id = call.data.split(":", 2)[2]
    info   = await svc.account_info(acc_id)
    if info is None:
        await call.answer("❌ Акаунт не знайдено", show_alert=True)
        return

    if "reader" not in info.professions:
        await call.answer("❌ Доступно тільки для reader", show_alert=True)
        return

    await state.set_state(MarkReadFSM.wait_input)
    await state.update_data(acc_id=acc_id, _nav_msg_id=call.message.message_id)  # type: ignore[union-attr]

    await call.message.answer(  # type: ignore[union-attr]
        f"✅ <b>Позначити глави як прочитані</b> для <code>{acc_id}</code>\n\n"
        "Введіть <b>список translit_name через кому</b>:\n\n"
        "<i>Приклад:</i>\n"
        "• <code>vsevedushchii-chitatel, naruto, one-piece</code>",
        reply_markup=cancel_add_kb(),
    )
    await call.answer()


@router.message(MarkReadFSM.wait_input)
async def fsm_mark_read_input(
    message: Message,
    state: FSMContext,
    svc: SchedulerService,
) -> None:
    text = (message.text or "").strip()
    data = await state.get_data()
    acc_id: str = data.get("acc_id", "")
    await state.clear()

    targets = [t.strip() for t in text.split(",") if t.strip()]
    if not targets:
        await message.answer("❌ Введіть хоча б один translit_name. Операцію скасовано.")
        return

    wait_msg = await message.answer(f"⏳ Позначаю як прочитані: {', '.join(targets)}…")

    ok, reason, result = await svc.mark_mangas_read(acc_id, targets=targets)

    if ok:
        marked: int = result.get("marked", 0)
        mangas: list[str] = result.get("mangas", [])
        
        await wait_msg.edit_text(
            f"✅ <b>Готово</b>\n"
            f"Позначено нових глав: <b>{marked}</b>\n"
            f"Манги: {', '.join(f'<code>{m}</code>' for m in mangas)}"
        )
    else:
        safe_reason = html.escape(str(reason))
        await wait_msg.edit_text(f"❌ Помилка:\n<code>{safe_reason}</code>")