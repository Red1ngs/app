"""
core/runtime/core_service.py — CoreService.

Інфраструктурні компоненти акаунта, що НЕ є «режимом роботи»
(на відміну від BaseProfession), але потребують lifecycle прив'язки
до конкретного Account.

Відмінності від BaseProfession:
  - НЕ реєструється в RequestRouter → не обробляє scheduler.ask()
  - НЕ має profession_id → не зберігається в БД як вибрана профессія
  - НЕ має handle_request() → немає dispatcher-логіки
  - setup()/teardown() — той самий lifecycle, але легший

Типовий CoreService: AuthService (зберігає user_name після логіну).

Lifecycle (керується ProfessionRegistry):
    startup:  bind(bot)       — прив'язка до конкретного Account
    shutdown: unbind()        — звільнення посилань
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.core_account import Account


class CoreService(ABC):
    """
    Легкий інфраструктурний компонент акаунта.

    Підклас реалізує:
        service_id   — унікальний str (для логування/дебагу)
        bind(bot)    — прив'язка до Account, підписки тощо
        unbind()     — звільнення ресурсів

    Підклас НЕ повинен:
        - реєструватись в scheduler як profession
        - зберігатись у БД як «активна профессія»
        - мати scheduling loop або sleep()
    """

    @property
    @abstractmethod
    def service_id(self) -> str:
        """Унікальний рядковий ідентифікатор для логування."""
        ...

    @abstractmethod
    async def bind(self, bot: "Account") -> None:
        """
        Прив'язує сервіс до конкретного Account.
        Викликається після setup professions, перед connect().
        """
        ...

    async def unbind(self) -> None:
        """
        Звільняє ресурси. Override за потреби.
        """
        pass

    def __repr__(self) -> str:
        return f"<{type(self).__name__} id={self.service_id!r}>"
