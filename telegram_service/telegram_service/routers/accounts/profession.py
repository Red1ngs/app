"""
accounts/profession.py
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from telegram_service.core_client import CoreServiceClient
from ._common import account_text, account_menu_kb, professions_manage_kb, cancel_add_kb

router = Router(name="accounts:profession")


class ProfessionSearchFSM(StatesGroup):
    wait_query = State()


async def _professions_text(acc_id: str, professions: list[str]) -> str:
    if not professions:
        return f"🎓 <b>Акаунт {acc_id}</b>\n\nПрофесій ще не призначено."
    lines = "\n".join(
        f"  {i+1}. <b>{name}</b>" for i, name in enumerate(professions)
    )
    return (
        f"🎓 <b>Акаунт {acc_id}</b>\n\n"
        f"Активні професії:\n{lines}\n\n"
        f"Професії акаунта визначають підключені монітори та доступний інструментарій."
    )


# ── Меню управління ───────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("acc:professions:"))
async def cb_professions_menu(call: CallbackQuery, svc: CoreServiceClient) -> None:
    acc_id = call.data.split(":", 2)[2]
    info   = await svc.account_info(acc_id)
    if info is None:
        await call.answer("❌ Акаунт не знайдено", show_alert=True)
        return
    all_names = await svc.known_professions()
    await call.message.edit_text(
        await _professions_text(acc_id, info.professions),
        reply_markup=professions_manage_kb(acc_id, info.professions, all_names),
    )
    await call.answer()


# ── Додавання ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("acc:prof_add:"))
async def cb_prof_add(call: CallbackQuery, svc: CoreServiceClient) -> None:
    parts = call.data.split(":", 3)
    if len(parts) < 4:
        await call.answer("❌ Помилка даних", show_alert=True)
        return

    acc_id, prof_name = parts[2], parts[3]
    ok, err = await svc.add_profession(acc_id, prof_name)
    if not ok:
        await call.answer(f"❌ {err}", show_alert=True)
        return

    info = await svc.account_info(acc_id)
    if info:
        all_names = await svc.known_professions()
        await call.message.edit_text(
            await _professions_text(acc_id, info.professions),
            reply_markup=professions_manage_kb(acc_id, info.professions, all_names),
        )
    await call.answer(f"✅ Професію {prof_name!r} додано")


# ── Видалення ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("acc:prof_remove:"))
async def cb_prof_remove(call: CallbackQuery, svc: CoreServiceClient) -> None:
    parts = call.data.split(":", 3)
    if len(parts) < 4:
        await call.answer("❌ Помилка даних", show_alert=True)
        return

    acc_id, prof_name = parts[2], parts[3]
    ok, err = await svc.remove_profession(acc_id, prof_name)
    if not ok:
        await call.answer(f"❌ {err}", show_alert=True)
        return

    info = await svc.account_info(acc_id)
    if info:
        all_names = await svc.known_professions()
        await call.message.edit_text(
            await _professions_text(acc_id, info.professions),
            reply_markup=professions_manage_kb(acc_id, info.professions, all_names),
        )
    await call.answer(f"✅ Професію {prof_name!r} видалено")


# ── Пошук професії для додавання ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("acc:prof_search_reset:"))
async def cb_prof_search_reset(call: CallbackQuery, svc: CoreServiceClient) -> None:
    acc_id = call.data.split(":", 2)[2]
    info = await svc.account_info(acc_id)
    if info is None:
        await call.answer("❌ Акаунт не знайдено", show_alert=True)
        return
    all_names = await svc.known_professions()
    await call.message.edit_text(
        await _professions_text(acc_id, info.professions),
        reply_markup=professions_manage_kb(acc_id, info.professions, all_names),
    )
    await call.answer()


@router.callback_query(F.data.startswith("acc:prof_search:"))
async def cb_prof_search_start(call: CallbackQuery, state: FSMContext) -> None:
    acc_id = call.data.split(":", 2)[2]
    await state.set_state(ProfessionSearchFSM.wait_query)
    await state.update_data(acc_id=acc_id, _nav_msg_id=call.message.message_id)
    await call.message.edit_text(
        f"🔎 <b>Пошук професії</b> для <code>{acc_id}</code>\n\nВведи частину назви:",
        reply_markup=cancel_add_kb(),
    )
    await call.answer()


@router.message(ProfessionSearchFSM.wait_query)
async def fsm_prof_search_query(message: Message, state: FSMContext, svc: CoreServiceClient) -> None:
    text = (message.text or "").strip()
    data = await state.get_data()
    acc_id = data.get("acc_id", "")
    nav_id = data.get("_nav_msg_id")
    await state.clear()

    try:
        await message.delete()
    except Exception:
        pass

    info = await svc.account_info(acc_id)
    if info is None:
        return

    text_out = await _professions_text(acc_id, info.professions)
    all_names = await svc.known_professions()
    kb = professions_manage_kb(acc_id, info.professions, all_names, query=text)

    bot_obj = message.bot
    chat = message.chat.id
    if nav_id and bot_obj:
        try:
            await bot_obj.edit_message_text(
                text_out, chat_id=chat, message_id=nav_id, reply_markup=kb, parse_mode="HTML",
            )
            return
        except Exception:
            pass
    await message.answer(text_out, reply_markup=kb)


# ── noop ──────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery) -> None:
    await call.answer()