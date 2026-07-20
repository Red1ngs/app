"""
accounts/slots.py
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from telegram_service.core_client import CoreServiceClient
from ._common import cancel_add_kb, account_text, account_menu_kb
from .profession_menu import profession_menu_registry

router = Router(name="accounts:slots")

profession_menu_registry.register(
    profession_id      = "reader",
    label              = "⚙️ Параметри читання",
    callback_template  = "acc:slots:{acc_id}",
)


# ── FSM ───────────────────────────────────────────────────────────────────────

class EditSlotsFSM(StatesGroup):
    wait_limit        = State()
    wait_include_tags = State()
    wait_exclude_tags = State()


# ── Entry point ───────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("acc:slots:"))
async def cb_slots(call: CallbackQuery, state: FSMContext, svc: CoreServiceClient) -> None:
    acc_id = call.data.split(":", 2)[2]
    info   = await svc.account_info(acc_id)
    if info is None:
        await call.answer("❌ Акаунт не знайдено", show_alert=True)
        return

    ok, reader_data = await svc.get_reader_state(acc_id)
    params = reader_data.get("reading_params", {})

    await state.set_state(EditSlotsFSM.wait_limit)
    # BUG FIX: зберігаємо nav_msg_id щоб редагувати inline-повідомлення, а не спамити новими
    await state.update_data(acc_id=acc_id, _nav_msg_id=call.message.message_id)  # type: ignore[union-attr]

    # BUG FIX: edit_text замість answer — лишаємось в тому ж inline-повідомленні
    await call.message.edit_text(  # type: ignore[union-attr]
        f"⚙️ <b>Параметри читання</b> для <code>{acc_id}</code>\n\n"
        f"Поточні налаштування:\n"
        f"  limit        = <b>{params.get('limit', 2)}</b>\n"
        f"  include_tags = <b>{params.get('include_tags') or '—'}</b>\n"
        f"  exclude_tags = <b>{params.get('exclude_tags') or '—'}</b>\n\n"
        "Введіть нове <b>limit</b> (кількість глав за раз, ціле число):",
        reply_markup=cancel_add_kb(),
    )
    await call.answer()


@router.message(EditSlotsFSM.wait_limit)
async def fsm_limit_input(message: Message, state: FSMContext, svc: CoreServiceClient) -> None:
    text = (message.text or "").strip()
    data = await state.get_data()
    nav_id = data.get("_nav_msg_id")
    bot_obj = message.bot
    chat = message.chat.id

    try:
        limit = int(text)
        if limit < 1:
            raise ValueError
    except ValueError:
        try:
            await message.delete()
        except Exception:
            pass
        if nav_id and bot_obj:
            try:
                await bot_obj.edit_message_text(
                    "❌ Введіть ціле число ≥ 1. Спробуйте ще раз:",
                    chat_id=chat, message_id=nav_id, reply_markup=cancel_add_kb(),
                )
                return
            except Exception:
                pass
        await message.answer("❌ Введіть ціле число ≥ 1. Спробуйте ще раз:", reply_markup=cancel_add_kb())
        return

    try:
        await message.delete()
    except Exception:
        pass

    await state.update_data(limit=limit)
    await state.set_state(EditSlotsFSM.wait_include_tags)

    prompt = (
        "Введіть <b>include_tags</b> через кому (або <code>-</code> щоб пропустити):\n\n"
        "<i>Приклад: <code>shounen, fantasy</code></i>"
    )
    if nav_id and bot_obj:
        try:
            await bot_obj.edit_message_text(
                prompt, chat_id=chat, message_id=nav_id,
                reply_markup=cancel_add_kb(), parse_mode="HTML",
            )
            return
        except Exception:
            pass
    await message.answer(prompt, reply_markup=cancel_add_kb())


@router.message(EditSlotsFSM.wait_include_tags)
async def fsm_include_tags_input(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    data = await state.get_data()
    nav_id = data.get("_nav_msg_id")
    bot_obj = message.bot
    chat = message.chat.id

    include_tags = None if text == "-" else [t.strip() for t in text.split(",") if t.strip()]

    try:
        await message.delete()
    except Exception:
        pass

    await state.update_data(include_tags=include_tags)
    await state.set_state(EditSlotsFSM.wait_exclude_tags)

    prompt = (
        "Введіть <b>exclude_tags</b> через кому (або <code>-</code> щоб пропустити):\n\n"
        "<i>Приклад: <code>ecchi, harem</code></i>"
    )
    if nav_id and bot_obj:
        try:
            await bot_obj.edit_message_text(
                prompt, chat_id=chat, message_id=nav_id,
                reply_markup=cancel_add_kb(), parse_mode="HTML",
            )
            return
        except Exception:
            pass
    await message.answer(prompt, reply_markup=cancel_add_kb())


@router.message(EditSlotsFSM.wait_exclude_tags)
async def fsm_exclude_tags_input(message: Message, state: FSMContext, svc: CoreServiceClient) -> None:
    text = (message.text or "").strip()
    exclude_tags = None if text == "-" else [t.strip() for t in text.split(",") if t.strip()]

    data   = await state.get_data()
    acc_id: str                  = data["acc_id"]
    limit:  int                  = data["limit"]
    include = data.get("include_tags")
    nav_id = data.get("_nav_msg_id")
    bot_obj = message.bot
    chat = message.chat.id
    await state.clear()

    try:
        await message.delete()
    except Exception:
        pass

    ok = await svc.update_reading_params(
        acc_id,
        limit        = limit,
        include_tags = include,
        exclude_tags = exclude_tags,
    )

    if ok:
        result_text = (
            f"✅ <b>Параметри читання оновлено</b>\n"
            f"  limit        = <b>{limit}</b>\n"
            f"  include_tags = <b>{include or '—'}</b>\n"
            f"  exclude_tags = <b>{exclude_tags or '—'}</b>"
        )
    else:
        result_text = "❌ Не вдалося оновити параметри. Перевірте лог."

    # BUG FIX: після завершення FSM повертаємось до меню акаунта через nav-повідомлення
    back_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="↩️ До меню акаунта", callback_data=f"acc:menu:{acc_id}"),
    ]])

    if nav_id and bot_obj:
        try:
            await bot_obj.edit_message_text(
                result_text, chat_id=chat, message_id=nav_id,
                reply_markup=back_kb, parse_mode="HTML",
            )
            return
        except Exception:
            pass
    await message.answer(result_text, reply_markup=back_kb)