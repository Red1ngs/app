"""
accounts/grouping.py

Модульна система групувань (категорій) для списку акаунтів.

Щоб додати НОВЕ групування — один виклик grouping_registry.register(...)
де завгодно в пакеті accounts. UI (accounts/search.py) підхоплює його
автоматично: з'явиться новий пункт у "🗂 Категорії".

keys_for(acc) повертає список пар (ключ, підпис_групи) — список, а не
одне значення, бо один акаунт може належати одразу кільком групам
(наприклад, кільком професіям одночасно).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from telegram_service.core_client import AccountInfo
from ._common import STATUS_LABEL, STATUS_TEXT

KeysFn = Callable[[AccountInfo], list[tuple[str, str]]]


@dataclass(frozen=True)
class GroupingSpec:
    id:       str
    label:    str
    emoji:    str
    keys_for: KeysFn


class GroupingRegistry:
    def __init__(self) -> None:
        self._items: dict[str, GroupingSpec] = {}
        self._order: list[str] = []

    def register(self, id: str, label: str, emoji: str, keys_for: KeysFn) -> None:
        if id in self._items:
            return
        self._items[id] = GroupingSpec(id=id, label=label, emoji=emoji, keys_for=keys_for)
        self._order.append(id)

    def get(self, id: str) -> Optional[GroupingSpec]:
        return self._items.get(id)

    def all(self) -> list[GroupingSpec]:
        return [self._items[i] for i in self._order]


grouping_registry = GroupingRegistry()


# ── Вбудовані групування ──────────────────────────────────────────────────────
#
# Приклад додавання власного групування (наприклад, за хостом проксі):
#
#   grouping_registry.register(
#       id="proxy_host", label="За проксі", emoji="🔗",
#       keys_for=lambda acc: [(acc.proxy.split(":")[0], acc.proxy)] if acc.proxy
#                            else [("__none__", "без проксі")],
#   )

grouping_registry.register(
    id="status", label="За статусом", emoji="🚦",
    keys_for=lambda acc: [(
        acc.status,
        f"{STATUS_LABEL.get(acc.status, '❓')} {STATUS_TEXT.get(acc.status, acc.status)}",
    )],
)

grouping_registry.register(
    id="profession", label="За професією", emoji="🎓",
    keys_for=lambda acc: (
        [(p, p) for p in acc.professions] if acc.professions
        else [("__none__", "без професії")]
    ),
)

grouping_registry.register(
    id="connected", label="За підключенням", emoji="🔌",
    keys_for=lambda acc: [("1", "🟢 Підключені")] if acc.is_connected else [("0", "🔴 Без сесії")],
)
