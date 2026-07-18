"""
accounts/search.py

Пошук і категорії акаунтів.

Сам UI тут НЕ знає про конкретні критерії пошуку чи групування —
він лише малює кнопки з того, що зареєстровано в filters.py /
grouping.py. Додавання нового критерію чи групування ніяк не
торкається цього файлу (див. коментарі-приклади там).
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.bot.services.scheduler_service import AccountInfo, SchedulerService
from ._common import accounts_list_kb, cancel_add_kb
from .filters import account_filter_registry
from .grouping import grouping_registry

router = Router(name="accounts:search")


class SearchFSM(StatesGroup):
    wait_query = State()


# ── Загальні helpers для екрану результатів ───────────────────────────────────

def _results_kb(accounts: list[AccountInfo], back_cb: str, back_label: str = "↩️ Назад") -> InlineKeyboardMarkup:
    return accounts_list_kb(
        accounts,
        show_add=False,
        extra_rows=[[
            InlineKeyboardButton(text="🔎 Новий пошук", callback_data="acc:search:menu"),
            InlineKeyboardButton(text=back_label, callback_data=back_cb),
        ]],
    )


def _empty_kb(back_cb: str, back_label: str = "↩️ Назад") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔎 Новий пошук", callback_data="acc:search:menu"),
        InlineKeyboardButton(text=back_label, callback_data=back_cb),
    ]])


async def _render_results(
    call: CallbackQuery,
    matched: list[AccountInfo],
    title: str,
    back_cb: str = "acc:list",
    back_label: str = "↩️ До списку",
) -> None:
    if not matched:
        await call.message.edit_text(  # type: ignore[union-attr]
            f"📭 {title}\n\nНічого не знайдено.",
            reply_markup=_empty_kb(back_cb, back_label),
        )
        return
    await call.message.edit_text(  # type: ignore[union-attr]
        f"<b>{title}</b>\nЗнайдено: {len(matched)}",
        reply_markup=_results_kb(matched, back_cb, back_label),
    )


# ── Меню пошуку ───────────────────────────────────────────────────────────────

def _search_menu_kb() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [[
        InlineKeyboardButton(text="🔤 Швидкий пошук (по всьому)", callback_data="acc:search:quick"),
    ]]
    for f in account_filter_registry.all():
        rows.append([InlineKeyboardButton(
            text=f"{f.emoji} {f.label}",
            callback_data=f"acc:search:filter:{f.id}",
        )])
    rows.append([InlineKeyboardButton(text="↩️ До списку", callback_data="acc:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "acc:search:menu")
async def cb_search_menu(call: CallbackQuery) -> None:
    await call.message.edit_text(  # type: ignore[union-attr]
        "🔎 <b>Пошук акаунтів</b>\n\nОбери критерій або скористайся швидким пошуком:",
        reply_markup=_search_menu_kb(),
    )
    await call.answer()


# ── Швидкий пошук одразу по кількох полях ─────────────────────────────────────

@router.callback_query(F.data == "acc:search:quick")
async def cb_search_quick_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SearchFSM.wait_query)
    await state.update_data(_nav_msg_id=call.message.message_id, _filter_id=None)  # type: ignore[union-attr]
    await call.message.edit_text(  # type: ignore[union-attr]
        "🔤 <b>Швидкий пошук</b>\n\nВведи ID, email або назву професії:",
        reply_markup=cancel_add_kb(),
    )
    await call.answer()


# ── Пошук за конкретним критерієм ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("acc:search:filter:"))
async def cb_search_pick_filter(call: CallbackQuery, state: FSMContext, svc: SchedulerService) -> None:
    filter_id = call.data.split(":", 3)[3]  # type: ignore[union-attr]
    spec = account_filter_registry.get(filter_id)
    if spec is None:
        await call.answer("❌ Невідомий критерій", show_alert=True)
        return

    if spec.kind == "choice":
        snapshot = await svc.snapshot()
        choices = spec.build_choices(snapshot.accounts)
        if not choices:
            await call.answer("📭 Немає значень для вибору", show_alert=True)
            return
        rows = [
            [InlineKeyboardButton(text=label, callback_data=f"acc:search:apply:{filter_id}:{value}")]
            for value, label in choices
        ]
        rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data="acc:search:menu")])
        await call.message.edit_text(  # type: ignore[union-attr]
            f"{spec.emoji} <b>{spec.label}</b>\n\nОбери значення:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        await call.answer()
        return

    await state.set_state(SearchFSM.wait_query)
    await state.update_data(_nav_msg_id=call.message.message_id, _filter_id=filter_id)  # type: ignore[union-attr]
    hint = f"\n\n<i>{spec.hint}</i>" if spec.hint else ""
    await call.message.edit_text(  # type: ignore[union-attr]
        f"{spec.emoji} <b>Пошук: {spec.label}</b>{hint}\n\nВведи текст для пошуку:",
        reply_markup=cancel_add_kb(),
    )
    await call.answer()


@router.message(SearchFSM.wait_query)
async def fsm_search_query(message: Message, state: FSMContext, svc: SchedulerService) -> None:
    text = (message.text or "").strip()
    data = await state.get_data()
    filter_id = data.get("_filter_id")
    nav_id = data.get("_nav_msg_id")
    await state.clear()

    try:
        await message.delete()
    except Exception:
        pass

    snapshot = await svc.snapshot()

    if not text:
        matched: list[AccountInfo] = []
    elif filter_id:
        spec = account_filter_registry.get(filter_id)
        matched = [a for a in snapshot.accounts if spec and spec.match(a, text)] if spec else []
    else:
        specs = account_filter_registry.quick_search_filters()
        matched = [a for a in snapshot.accounts if any(s.match(a, text) for s in specs)]

    title = f"🔎 Результати пошуку «{text}»" if text else "🔎 Результати пошуку"

    bot_obj = message.bot
    chat = message.chat.id
    if nav_id and bot_obj:
        try:
            if not matched:
                await bot_obj.edit_message_text(
                    f"📭 {title}\n\nНічого не знайдено.",
                    chat_id=chat, message_id=nav_id,
                    reply_markup=_empty_kb("acc:list", "↩️ До списку"),
                    parse_mode="HTML",
                )
            else:
                await bot_obj.edit_message_text(
                    f"<b>{title}</b>\nЗнайдено: {len(matched)}",
                    chat_id=chat, message_id=nav_id,
                    reply_markup=_results_kb(matched, "acc:list", "↩️ До списку"),
                    parse_mode="HTML",
                )
            return
        except Exception:
            pass

    if matched:
        await message.answer(
            f"<b>{title}</b>\nЗнайдено: {len(matched)}",
            reply_markup=_results_kb(matched, "acc:list", "↩️ До списку"),
        )
    else:
        await message.answer(
            f"📭 {title}\n\nНічого не знайдено.",
            reply_markup=_empty_kb("acc:list", "↩️ До списку"),
        )


@router.callback_query(F.data.startswith("acc:search:apply:"))
async def cb_search_apply(call: CallbackQuery, svc: SchedulerService) -> None:
    parts = call.data.split(":", 4)  # type: ignore[union-attr]
    if len(parts) < 5:
        await call.answer("❌ Помилка даних", show_alert=True)
        return
    _, _, _, filter_id, value = parts
    spec = account_filter_registry.get(filter_id)
    if spec is None:
        await call.answer("❌ Невідомий критерій", show_alert=True)
        return

    snapshot = await svc.snapshot()
    matched = [a for a in snapshot.accounts if spec.match(a, value)]
    await _render_results(call, matched, f"{spec.emoji} {spec.label}: {value}")
    await call.answer()


# ── Категорії (групування) ────────────────────────────────────────────────────

def _group_menu_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{g.emoji} {g.label}", callback_data=f"acc:group:pick:{g.id}")]
        for g in grouping_registry.all()
    ]
    rows.append([InlineKeyboardButton(text="↩️ До списку", callback_data="acc:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "acc:group:menu")
async def cb_group_menu(call: CallbackQuery) -> None:
    await call.message.edit_text(  # type: ignore[union-attr]
        "🗂 <b>Категорії</b>\n\nОбери групування:",
        reply_markup=_group_menu_kb(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("acc:group:pick:"))
async def cb_group_pick(call: CallbackQuery, svc: SchedulerService) -> None:
    grouping_id = call.data.split(":", 3)[3]  # type: ignore[union-attr]
    spec = grouping_registry.get(grouping_id)
    if spec is None:
        await call.answer("❌ Невідоме групування", show_alert=True)
        return

    snapshot = await svc.snapshot()
    counts: dict[str, tuple[str, int]] = {}
    for acc in snapshot.accounts:
        for key, key_label in spec.keys_for(acc):
            label, cnt = counts.get(key, (key_label, 0))
            counts[key] = (label, cnt + 1)

    if not counts:
        await call.answer("📭 Немає акаунтів для групування", show_alert=True)
        return

    rows = [
        [InlineKeyboardButton(
            text=f"{label} ({cnt})",
            callback_data=f"acc:group:show:{grouping_id}:{key}",
        )]
        for key, (label, cnt) in sorted(counts.items(), key=lambda kv: kv[1][0])
    ]
    rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data="acc:group:menu")])
    await call.message.edit_text(  # type: ignore[union-attr]
        f"{spec.emoji} <b>{spec.label}</b>\n\nОбери групу:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await call.answer()


@router.callback_query(F.data.startswith("acc:group:show:"))
async def cb_group_show(call: CallbackQuery, svc: SchedulerService) -> None:
    parts = call.data.split(":", 4)  # type: ignore[union-attr]
    if len(parts) < 5:
        await call.answer("❌ Помилка даних", show_alert=True)
        return
    _, _, _, grouping_id, key = parts
    spec = grouping_registry.get(grouping_id)
    if spec is None:
        await call.answer("❌ Невідоме групування", show_alert=True)
        return

    snapshot = await svc.snapshot()
    matched = [a for a in snapshot.accounts if any(k == key for k, _ in spec.keys_for(a))]
    await _render_results(
        call, matched, f"{spec.emoji} {spec.label}",
        back_cb=f"acc:group:pick:{grouping_id}", back_label="↩️ Назад",
    )
    await call.answer()
