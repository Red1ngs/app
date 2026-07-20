"""
accounts/add.py

FSM додавання нового акаунта: ID → Email → Password → Proxy.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from telegram_service.core_client import CoreServiceClient
from ._common import cancel_add_kb, make_editor

router = Router(name="accounts:add")

_TITLE = "➕ <b>Додавання акаунта</b>\n\n"

# ── FSM ───────────────────────────────────────────────────────────────────────

class AddAccountFSM(StatesGroup):
    wait_id       = State()
    wait_email    = State()
    wait_password = State()
    wait_proxy    = State()


# ── Успішне завершення ────────────────────────────────────────────────────────

def _success_kb(acc_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎓 Призначити професію",
                              callback_data=f"acc:professions:{acc_id}")],
        [InlineKeyboardButton(text="📋 До списку", callback_data="acc:list")],
    ])


def _error_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="↩️ Назад", callback_data="acc:list"),
    ]])


# ── Старт ─────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "acc:add")
async def cb_add_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddAccountFSM.wait_id)
    await state.update_data(_nav_msg_id=call.message.message_id)  # type: ignore[union-attr]
    await call.message.edit_text(  # type: ignore[union-attr]
        _TITLE + "Крок 1/4: Введи унікальний ID\n<i>Наприклад: acc_04</i>",
        reply_markup=cancel_add_kb(),
    )
    await call.answer()


# ── Крок 1: ID ────────────────────────────────────────────────────────────────

@router.message(AddAccountFSM.wait_id)
async def fsm_wait_id(message: Message, state: FSMContext, svc: CoreServiceClient) -> None:
    acc_id = (message.text or "").strip()
    _edit  = make_editor(message, await state.get_data())

    if not acc_id or " " in acc_id:
        await _edit(_TITLE + "❌ ID не може бути порожнім або містити пробіли.\nКрок 1/4: Введи ID ще раз:", cancel_add_kb())
        return
    if acc_id in await svc.account_ids():
        await _edit(_TITLE + f"❌ Акаунт <code>{acc_id}</code> вже існує.\nКрок 1/4: Введи інший ID:", cancel_add_kb())
        return

    await state.update_data(acc_id=acc_id)
    await state.set_state(AddAccountFSM.wait_email)
    await _edit(_TITLE + f"✅ ID: <code>{acc_id}</code>\n\nКрок 2/4: Введи email:", cancel_add_kb())


# ── Крок 2: Email ─────────────────────────────────────────────────────────────

@router.message(AddAccountFSM.wait_email)
async def fsm_wait_email(message: Message, state: FSMContext, svc: CoreServiceClient) -> None:
    email = (message.text or "").strip()
    _edit = make_editor(message, await state.get_data())

    if "@" not in email:
        await _edit(_TITLE + "❌ Схоже, це не email.\nКрок 2/4: Спробуй ще раз:", cancel_add_kb())
        return

    # Перевіримо чи email вже використовується
    existing_id = await svc.find_account_by_email(email)
    if existing_id:
        await _edit(
            _TITLE + f"❌ Email <code>{email}</code> вже використовується аккаунтом <code>{existing_id}</code>.\n\n"
            "Крок 2/4: Введи інший email:",
            cancel_add_kb(),
        )
        return

    await state.update_data(email=email)
    await state.set_state(AddAccountFSM.wait_password)
    await _edit(
        _TITLE + f"✅ Email: <code>{email}</code>\n\n"
        "Крок 3/4: Введи пароль:\n<i>Повідомлення буде одразу видалено</i>",
        cancel_add_kb(),
    )


# ── Крок 3: Password ──────────────────────────────────────────────────────────

@router.message(AddAccountFSM.wait_password)
async def fsm_wait_password(message: Message, state: FSMContext) -> None:
    password = (message.text or "").strip()
    try:
        await message.delete()
    except Exception:
        pass
    _edit = make_editor(message, await state.get_data(), already_deleted=True)

    if not password:
        await _edit(_TITLE + "❌ Пароль не може бути порожнім.\nКрок 3/4: Введи пароль:", cancel_add_kb())
        return

    await state.update_data(password=password)
    await state.set_state(AddAccountFSM.wait_proxy)
    await _edit(
        _TITLE + "✅ Пароль збережено\n\nКрок 4/4: Введи проксі або пропусти:",
        InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⏭ Без проксі", callback_data="acc:add_no_proxy"),
            InlineKeyboardButton(text="❌ Скасувати",  callback_data="acc:add_cancel"),
        ]]),
    )


# ── Крок 4: Proxy ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "acc:add_no_proxy")
async def cb_no_proxy(call: CallbackQuery, state: FSMContext, svc: CoreServiceClient) -> None:
    await _finish_from_call(call, state, svc, proxy="")


@router.message(AddAccountFSM.wait_proxy)
async def fsm_wait_proxy(message: Message, state: FSMContext, svc: CoreServiceClient) -> None:
    proxy = (message.text or "").strip()
    try:
        await message.delete()
    except Exception:
        pass
    await _finish_from_msg(message, state, svc, proxy=proxy)


# ── Скасування ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "acc:add_cancel")
async def cb_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text(  # type: ignore[union-attr]
        "❌ Скасовано.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="↩️ До списку", callback_data="acc:list"),
        ]]),
    )
    await call.answer()


# ── Спільна логіка завершення ─────────────────────────────────────────────────

async def _finish_from_call(
    call:  CallbackQuery,
    state: FSMContext,
    svc:   CoreServiceClient,
    proxy: str,
) -> None:
    data = await state.get_data()
    await state.clear()
    acc_id   = data.get("acc_id")
    email    = data.get("email")
    password = data.get("password")
    if not acc_id or not email or not password:
        await call.message.edit_text(
            "❌ Сесія додавання загублена. Почніть заново.",
            reply_markup=_error_kb(),
        )
        return
    ok, err = await svc.add_account(acc_id, email, password, proxy)
    if ok:
        await call.message.edit_text(  # type: ignore[union-attr]
            f"✅ Акаунт <code>{acc_id}</code> додано!\n\n"
            "Тепер <b>призначте професію</b> щоб запустити монітори.",
            reply_markup=_success_kb(acc_id),
        )
    else:
        await call.message.edit_text(  # type: ignore[union-attr]
            f"❌ Помилка при додаванні:\n<code>{err}</code>",
            reply_markup=_error_kb(),
        )
    await call.answer()


async def _finish_from_msg(
    message: Message,
    state:   FSMContext,
    svc:     CoreServiceClient,
    proxy:   str,
) -> None:
    data   = await state.get_data()
    nav_id = data.get("_nav_msg_id")
    bot    = message.bot
    chat   = message.chat.id
    await state.clear()

    acc_id   = data.get("acc_id")
    email    = data.get("email")
    password = data.get("password")
    if not acc_id or not email or not password:
        await message.answer(
            "❌ Сесія додавання загублена. Почніть заново.",
            reply_markup=_error_kb(),
        )
        return
    
    ok, err = await svc.add_account(acc_id, email, password, proxy)
    if ok:
        text = (
            f"✅ Акаунт <code>{acc_id}</code> додано!\n\n"
            "Тепер <b>призначте професію</b> щоб запустити монітори."
        )
        kb = _success_kb(acc_id)
    else:
        text = f"❌ Помилка при додаванні:\n<code>{err}</code>"
        kb   = _error_kb()

    if nav_id and bot:
        try:
            await bot.edit_message_text(text, chat_id=chat, message_id=nav_id,
                                        reply_markup=kb, parse_mode="HTML")
            return
        except Exception:
            pass
    await message.answer(text, reply_markup=kb)