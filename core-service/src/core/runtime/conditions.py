"""
conditions.py — умови guard для акаунтів і профілей.

Condition  — предикат (inv: Inventories) → bool.
             True = все добре, False = guard спрацював → зупинка.

RateGuard  — вбудований rate-limiter.
             Рахує кількість «тіків» за sliding window.
             Якщо перевищено — guard повертає False.

Приклади:
    guard = not_(has("is_banned"))
    guard = all_(not_(has("is_banned")), RateGuard(max_calls=100, window=60))
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from src.utils.time import monotonic

if TYPE_CHECKING:
    from src.core.inventory.model import Inventories

Condition = Callable[["Inventories"], bool]


# ── Прості комбінатори ────────────────────────────────────────────────────────

def has(key: str) -> Condition:
    """True якщо inv.personal.data містить truthy значення за ключем."""
    def check(inv: "Inventories") -> bool:
        return bool(inv.personal.data.get(key))
    return check


def not_(cond: Condition) -> Condition:
    def check(inv: "Inventories") -> bool:
        return not cond(inv)
    return check


def all_(*conds: Condition) -> Condition:
    """True якщо всі умови True."""
    def check(inv: "Inventories") -> bool:
        return all(c(inv) for c in conds)
    return check


def any_(*conds: Condition) -> Condition:
    """True якщо хоча б одна умова True."""
    def check(inv: "Inventories") -> bool:
        return any(c(inv) for c in conds)
    return check


# ── RateGuard ─────────────────────────────────────────────────────────────────

@dataclass
class RateGuard:
    """
    Guard що обмежує частоту спрацювань тригера / задач.

    Рахує кількість tick() за sliding window (секунди).
    Якщо кількість перевищує max_calls — guard повертає False,
    що зупиняє акаунт або profession негайно.

    Використання як guard profession:
        RateGuard(max_calls=60, window=60)   # не більше 60 запитів/хв

    Використання як самостійний лічильник (не guard):
        rate = RateGuard(max_calls=100, window=60)
        rate.tick()           # реєструємо запит
        rate.is_ok()          # True якщо ще в межах

    Як Condition (guard):
        guard = rate.as_condition()
        # або напряму — RateGuard є callable:
        guard = rate   # RateGuard.__call__(inv) → bool
    """
    max_calls: int
    window:    float          # секунди

    _calls:    deque[float] = field(default_factory=deque, init=False)
    _lock:     threading.Lock = field(default_factory=threading.Lock, init=False)

    def tick(self) -> None:
        """Реєструє один виклик. Викликати перед або після кожного запиту."""
        now = monotonic()
        with self._lock:
            self._calls.append(now)
            self._trim(now)

    def is_ok(self) -> bool:
        """True якщо кількість викликів за window не перевищує max_calls."""
        now = monotonic()
        with self._lock:
            self._trim(now)
            return len(self._calls) < self.max_calls

    def reset(self) -> None:
        with self._lock:
            self._calls.clear()

    @property
    def current_rate(self) -> int:
        """Кількість викликів за поточне вікно."""
        now = monotonic()
        with self._lock:
            self._trim(now)
            return len(self._calls)

    # ── Callable / Condition ──────────────────────────────────────────────────

    def __call__(self, inv: "Inventories") -> bool:
        """
        Використовується як Condition безпосередньо.
        inv ігнорується — RateGuard не залежить від стану інвентаря.
        """
        return self.is_ok()

    def as_condition(self) -> Condition:
        """Повертає себе як Condition (для явної типізації)."""
        return self

    # ── Internal ──────────────────────────────────────────────────────────────

    def _trim(self, now: float) -> None:
        cutoff = now - self.window
        while self._calls and self._calls[0] < cutoff:
            self._calls.popleft()

    def __repr__(self) -> str:
        return (
            f"<RateGuard {self.current_rate}/{self.max_calls} "
            f"per {self.window:.0f}s>"
        )