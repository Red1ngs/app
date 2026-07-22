"""
src/mangabuff/session/http_result.py

Контракт повернення бізнес-методів.
Не залежить від жодного іншого модуля проєкту.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Awaitable, Callable, Generic, Literal, Optional, TypeVar, ParamSpec, Concatenate, cast

from account_service_client import AccountAuthError, AccountUnavailableError
from src.utils.logging import get_logger as log


# ── Типи ─────────────────────────────────────────────────────────────────────

T = TypeVar("T")
R = TypeVar("R")
P = ParamSpec("P")

HttpMethodStr = Literal["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"]


class FailReason(Enum):
    NETWORK        = auto()   # таймаут, з'єднання відхилено, будь-який виняток
    AUTH           = auto()   # account-service: сесія прострочена / акаунт не підключений
    NOT_FOUND      = auto()   # 404
    SERVER         = auto()   # 5xx або неочікуваний статус
    BAD_DATA       = auto()   # 200, але тіло порожнє або не те що очікували
    DENIED         = auto()   # сервер явно відмовив (403, success=false тощо)
    LIMIT_EXHAUSTED = auto()  # 403 «ліміт на сьогодні вичерпано» — не помилка,
                               # а розбіжність з реальним станом на сайті;
                               # caller фіксує лічильник (напр. hits_left=0)


@dataclass(frozen=True)
class HttpResult(Generic[T]):
    ok:     bool
    data:   Optional[T]          = None
    reason: Optional[FailReason] = None

    def __post_init__(self) -> None:
        if self.ok and self.reason is not None:
            raise ValueError("Успішний результат не повинен мати reason")
        if not self.ok and self.reason is None:
            raise ValueError("Невдалий результат повинен мати reason")

    def __bool__(self) -> bool:
        return self.ok


# ── Конструктори ──────────────────────────────────────────────────────────────

def http_success(data: T) -> HttpResult[T]:
    """Створює успішний HttpResult з конкретним значенням."""
    return HttpResult(ok=True, data=data)


def http_success_none() -> "HttpResult[T]":
    """
    Створює успішний HttpResult без даних, типізований під очікуваний T
    у місці виклику.

    HttpResult.data — Optional[T] для будь-якого T, тож runtime-значення
    None коректне незалежно від T. Але голий `-> HttpResult[T]" де T
    з'являється лише в поверненні — reportInvalidTypeVarUse (Pyright не
    гарантує виведення такого T). Тому будуємо конкретний HttpResult[None]
    і cast'уємо тип статично — це чесний спосіб сказати "я знаю, що це
    безпечно для будь-якого T", а не покладатись на непідтримувану
    поведінку виведення.
    """
    return cast("HttpResult[T]", HttpResult(ok=True, data=None))


def http_fail(reason: FailReason, data: Optional[Any] = None) -> HttpResult[Any]:
    """
    Створює невдалий HttpResult.

    data — опційний контекст для caller'а навіть при невдачі (напр.
    LIMIT_EXHAUSTED несе {"hits_left": 0}, щоб інвентар зафіксував реальний
    стан, а не лишався в застарілому значенні до наступного успішного запиту).
    """
    return HttpResult(ok=False, reason=reason, data=data)


# ── Декоратор ────────────────────────────────────────────────────────────────

def http_call(
    func: "Callable[Concatenate[Any, P], Awaitable[HttpResult[R]]]",
) -> "Callable[Concatenate[Any, P], Awaitable[HttpResult[R]]]":
    """
    Обгортає бізнес-метод BotSession:
      - AccountAuthError / AccountUnavailableError (account-service повідомив
        про прострочену сесію чи непідключений акаунт) → FailReason.AUTH
      - будь-який інший виняток → FailReason.NETWORK
    """
    @functools.wraps(func)
    async def wrapper(self: Any, *args: P.args, **kwargs: P.kwargs) -> HttpResult[R]:
        try:
            return await func(self, *args, **kwargs)
        except (AccountAuthError, AccountUnavailableError) as e:
            log().error(f"  → {func.__name__}: auth error ({e})")
            return http_fail(FailReason.AUTH)
        except Exception as e:
            log().error(f"  → {func.__name__}: {e}")
            return http_fail(FailReason.NETWORK)
    return wrapper