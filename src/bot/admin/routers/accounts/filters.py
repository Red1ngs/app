"""
accounts/filters.py

Модульна система критеріїв пошуку акаунтів.

Щоб додати НОВИЙ критерій пошуку (наприклад, за тегом чи будь-яким іншим
полем) — досить одного виклику account_filter_registry.register(...) де
завгодно в пакеті accounts (за прикладом нижче). Решта UI (accounts/search.py)
підхоплює новий фільтр автоматично, нічого більше міняти не треба.

kind="text"   — вводиться довільний рядок (FSM), match(acc, query)
kind="choice" — показуються готові кнопки-варіанти з choices(accounts)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from src.bot.services.scheduler_service import AccountInfo
from ._common import STATUS_TEXT

MatchFn   = Callable[[AccountInfo, str], bool]
ChoicesFn = Callable[[list[AccountInfo]], list[tuple[str, str]]]  # -> [(value, label), ...]


@dataclass(frozen=True)
class AccountFilterSpec:
    id:           str
    label:        str
    emoji:        str
    kind:         str  # "text" | "choice"
    match:        MatchFn
    choices:      Optional[ChoicesFn] = None
    quick_search: bool = True   # чи бере участь у "швидкому пошуку по всьому"
    hint:         str  = ""

    def build_choices(self, accounts: list[AccountInfo]) -> list[tuple[str, str]]:
        if self.choices is None:
            return []
        return self.choices(accounts)


class AccountFilterRegistry:
    def __init__(self) -> None:
        self._filters: dict[str, AccountFilterSpec] = {}
        self._order:   list[str] = []

    def register(
        self,
        id:           str,
        label:        str,
        emoji:        str,
        match:        MatchFn,
        *,
        kind:         str = "text",
        choices:      Optional[ChoicesFn] = None,
        quick_search: bool = True,
        hint:         str = "",
    ) -> None:
        if id in self._filters:
            return  # ідемпотентно: модуль може імпортуватись кілька разів
        self._filters[id] = AccountFilterSpec(
            id=id, label=label, emoji=emoji, kind=kind,
            match=match, choices=choices, quick_search=quick_search, hint=hint,
        )
        self._order.append(id)

    def get(self, id: str) -> Optional[AccountFilterSpec]:
        return self._filters.get(id)

    def all(self) -> list[AccountFilterSpec]:
        return [self._filters[i] for i in self._order]

    def quick_search_filters(self) -> list[AccountFilterSpec]:
        return [f for f in self.all() if f.quick_search]


account_filter_registry = AccountFilterRegistry()


# ── Вбудовані фільтри ─────────────────────────────────────────────────────────
#
# Приклад додавання власного критерію (наприклад, за поштовим доменом):
#
#   account_filter_registry.register(
#       id="email_domain", label="Домен пошти", emoji="🌐",
#       match=lambda acc, q: (acc.email or "").lower().endswith(q.lower()),
#   )

account_filter_registry.register(
    id="id", label="ID акаунта", emoji="🆔",
    match=lambda acc, q: q.lower() in acc.account_id.lower(),
    hint="Наприклад: acc_04",
)

account_filter_registry.register(
    id="email", label="Email", emoji="📧",
    match=lambda acc, q: q.lower() in (acc.email or "").lower(),
    hint="Наприклад: gmail.com",
)

account_filter_registry.register(
    id="profession", label="Професія", emoji="🎓",
    match=lambda acc, q: q.lower() in " ".join(acc.professions).lower(),
    kind="choice",
    choices=lambda accounts: sorted({(p, p) for acc in accounts for p in acc.professions}),
)

account_filter_registry.register(
    id="status", label="Статус", emoji="🚦",
    match=lambda acc, q: q.upper() == acc.status.upper(),
    kind="choice",
    choices=lambda accounts: sorted({
        (acc.status, f"{STATUS_TEXT.get(acc.status, acc.status)} ({acc.status})")
        for acc in accounts
    }),
    quick_search=False,
)

account_filter_registry.register(
    id="connected", label="Підключення", emoji="🔌",
    match=lambda acc, q: (q == "1") == acc.is_connected,
    kind="choice",
    choices=lambda accounts: [("1", "🟢 Підключені"), ("0", "🔴 Без сесії")],
    quick_search=False,
)

account_filter_registry.register(
    id="proxy", label="Проксі", emoji="🔗",
    match=lambda acc, q: q.lower() in (acc.proxy or "").lower(),
    hint="Наприклад: частина IP або хоста",
)
