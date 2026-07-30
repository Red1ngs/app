"""
accounts/edit.py

FSM вільної зміни облікових даних акаунта "на ходу": email, проксі.

На відміну від add.py (де ID/email/пароль/проксі задаються один раз при
створенні), тут кожне поле редагується незалежно, в один крок, і working
не обов'язково зупиняти сесію заздалегідь — account-service сам
перепідключає її з новими даними, якщо треба (див.
AccountManager.update_account на його боці).
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from telegram_service.core_client import CoreServiceClient
from ._common import account_text, account_menu_kb, cancel_edit_kb, make_editor
from ...utils.proxy_utils import normalize_proxy

router = Router(name="accounts:edit")


class EditAccountFSM(StatesGroup):
    wait_email = State()
    wait_proxy = State()


async def _back_to_menu(svc: CoreServiceClient, acc_id: str) -> tuple[str, InlineKeyboardMarkup]:
    info = await svc.account_info(acc_id)
    if info is None:
        return f"❌ Акаунт <code>{acc_id}</code> не знайдено.", InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="↩️ До списку", callback_data="acc:list")]]
        )
    return account_text(info), account_menu_kb(acc_id, info.status, list(info.professions), info.is_connected)


# ── Email ─────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("acc:edit_email:"))
async def cb_edit_email_start(call: CallbackQuery, state: FSMContext, svc: CoreServiceClient) -> None:
    acc_id = call.data.split(":", 2)[2]
    if await svc.account_info(acc_id) is None:
        await call.answer("❌ Акаунт не знайдено", show_alert=True)
        return
    await state.set_state(EditAccountFSM.wait_email)
    await state.update_data(acc_id=acc_id, _nav_msg_id=call.message.message_id)  # type: ignore[union-attr]
    await call.message.edit_text(  # type: ignore[union-attr]
        f"✉️ <b>Зміна email</b> для <code>{acc_id}</code>\n\nВведи новий email:",
        reply_markup=cancel_edit_kb(acc_id),
    )
    await call.answer()


@router.message(EditAccountFSM.wait_email)
async def fsm_wait_email(message: Message, state: FSMContext, svc: CoreServiceClient) -> None:
    data   = await state.get_data()
    acc_id = data.get("acc_id")
    _edit  = make_editor(message, data)
    email  = (message.text or "").strip()

    if not acc_id:
        await state.clear()
        await _edit("❌ Сесія редагування загублена. Відкрий акаунт ще раз.")
        return

    if "@" not in email:
        await _edit(
            f"✉️ <b>Зміна email</b> для <code>{acc_id}</code>\n\n"
            "❌ Схоже, це не email. Спробуй ще раз:",
            cancel_edit_kb(acc_id),
        )
        return

    existing_id = await svc.find_account_by_email(email)
    if existing_id and existing_id != acc_id:
        await _edit(
            f"✉️ <b>Зміна email</b> для <code>{acc_id}</code>\n\n"
            f"❌ Email <code>{email}</code> вже використовується акаунтом <code>{existing_id}</code>.\n"
            "Введи інший:",
            cancel_edit_kb(acc_id),
        )
        return

    await state.clear()
    ok, err = await svc.update_email(acc_id, email)
    if ok:
        text, kb = await _back_to_menu(svc, acc_id)
        await _edit(f"✅ Email оновлено на <code>{email}</code>.\n\n{text}", kb)
    else:
        await _edit(
            f"❌ Не вдалось оновити email:\n<code>{err}</code>",
            InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="↩️ До акаунта", callback_data=f"acc:menu:{acc_id}"),
            ]]),
        )


# ── Проксі ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("acc:edit_proxy:"))
async def cb_edit_proxy_start(call: CallbackQuery, state: FSMContext, svc: CoreServiceClient) -> None:
    acc_id = call.data.split(":", 2)[2]
    if await svc.account_info(acc_id) is None:
        await call.answer("❌ Акаунт не знайдено", show_alert=True)
        return
    await state.set_state(EditAccountFSM.wait_proxy)
    await state.update_data(acc_id=acc_id, _nav_msg_id=call.message.message_id)  # type: ignore[union-attr]
    await call.message.edit_text(  # type: ignore[union-attr]
        f"🌐 <b>Зміна проксі</b> для <code>{acc_id}</code>\n\n"
        "Введи новий проксі, або «-» щоб прибрати проксі (працювати напряму):",
        reply_markup=cancel_edit_kb(acc_id),
    )
    await call.answer()


@router.message(EditAccountFSM.wait_proxy)
async def fsm_wait_proxy(message: Message, state: FSMContext, svc: CoreServiceClient) -> None:
    data   = await state.get_data()
    acc_id = data.get("acc_id")
    raw    = (message.text or "").strip()
    _edit  = make_editor(message, data)

    if not acc_id:
        await state.clear()
        await _edit("❌ Сесія редагування загублена. Відкрий акаунт ще раз.")
        return

    proxy: str | None
    if raw == "-":
        proxy = None
    else:
        try:
            proxy = normalize_proxy(raw)
        except ValueError as e:
            await _edit(
                f"🌐 <b>Зміна проксі</b> для <code>{acc_id}</code>\n\n"
                f"❌ {e}\n\nСпробуй ще раз, або «-» щоб прибрати проксі:",
                cancel_edit_kb(acc_id),
            )
            return

    await state.clear()
    ok, err = await svc.update_proxy(acc_id, proxy)
    if ok:
        shown = proxy or "<i>без проксі</i>"
        text, kb = await _back_to_menu(svc, acc_id)
        await _edit(f"✅ Проксі оновлено: {shown}\n\n{text}", kb)
    else:
        await _edit(
            f"❌ Не вдалось оновити проксі:\n<code>{err}</code>",
            InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="↩️ До акаунта", callback_data=f"acc:menu:{acc_id}"),
            ]]),
        )
