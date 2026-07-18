"""
src/core/runtime/profession.py — BaseProfession + RequestResult.

Архітектурна роль:
    BaseProfession — абстрактний stateful domain agent.
    Підкласи реалізують конкретну бізнес-логіку (Reader, Daily, Trader...).

    Profession знає лише про:
      - свій inventory namespace (bot.inventory.{attr})
      - scheduler як спосіб комунікації (emit_event / ask)

    Profession НЕ знає про:
      - інші Profession
      - scheduling loop

Де зберігається стан?
    bot.inventory.{namespace} (Inventories = BlackBoard).
    Persistується автоматично після завершення handle_request (через RequestRouter)
    та після emit_event що містить account_id (через EventDrivenScheduler).
    Profession і Monitor НЕ повинні викликати bot.repo.inventory.save() напряму.

Lifecycle:
    setup(scheduler, account_id)  — підписки + реєстрація
    restore_state(bot)            — відновлення in-memory state з inventory
    handle_request(intent, data)  — відповідь на scheduler.ask()
    on_event(event_name, payload) — реакція на EventBus події
    teardown(scheduler, account_id) — звільнення ресурсів
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, Optional, List, Type

if TYPE_CHECKING:
    from src.core.core_account import Account
    from src.core.runtime.request_router import RequestContext
    from src.core.runtime.scheduler import EventDrivenScheduler

from src.core.logging.loggers import get_logger
log = get_logger("runtime.profession")


class BaseProfession(ABC):
    """
    Stateful behavioral component — autonomous domain agent.

    Підклас реалізує:
        profession_id    — унікальний str ідентифікатор
        setup()          — реєструє event-підписки
        handle_request() — відповідає на scheduler.ask()

    Підклас може перевизначити:
        on_event()       — реакція на EventBus події (за замовч. нічого)
        restore_state()  — відновлення in-memory cache з bot.inventory
        teardown()       — звільнення ресурсів при видаленні акаунта
        check_guard()    — чи може profession бути активною

    Підклас НЕ повинен:
        - викликати інші Profession напряму
        - містити scheduling loop або sleep()
        - зберігати критичний стан лише in-memory
    """

    # ── Abstract ──────────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def profession_id(self) -> str:
        """Унікальний рядковий ідентифікатор. Наприклад: "trader", "reader"."""
        ...

    @abstractmethod
    async def setup(self, scheduler: "EventDrivenScheduler", account_id: str) -> None:
        """
        Викликається один раз при додаванні акаунта в scheduler.

        Тут profession:
          - підписується на події: scheduler.subscribe("event", self._on_event)
          - може зберегти посилання: self._scheduler = scheduler

        НЕ робить IO тут — тільки реєстрація.
        """
        ...

    @abstractmethod
    async def handle_request(
        self,
        intent: str,
        data: dict[str, Any],
        ctx: "RequestContext",
    ) -> "RequestResult":
        """
        Обробляє запит від scheduler.ask().

        intent — що хоче caller ("initiate_trade", "get_stats", ...)
        data   — параметри запиту
        ctx    — контекст: account_id, caller, bot

        Повертає RequestResult.approve() або RequestResult.deny().
        """
        ...

    # ── Optional overrides ────────────────────────────────────────────────────

    async def restore_state(self, bot: "Account") -> None:
        """
        Відновлює in-memory state після restart з bot.inventory.

        Викликається після setup(). bot.inventory вже завантажений з БД.
        """
        pass

    async def teardown(self, scheduler: "EventDrivenScheduler", account_id: str) -> None:
        """
        Викликається при видаленні акаунта.
        Override щоб скинути resources.
        """
        pass

    async def on_event(self, event_name: str, payload: dict[str, Any]) -> None:
        """
        Реагує на подію з EventBus.
        За замовч. нічого не робить. Override для конкретної реакції.
        """
        pass

    def check_guard(self, bot: "Account") -> bool:
        """
        Перевіряє чи може Profession бути активною.
        True = все добре. False = profession suspended.
        """
        return True

    def _persist_inventory(self, bot: "Account") -> None:
        """
        Негайно зберігає поточний інвентар акаунта в БД.
        Використовується для критичних змін стану, які мають бути
        відновлені навіть якщо процес зупиниться під час наступного кроку.
        """
        try:
            if getattr(bot, "repo", None) is not None and getattr(bot.repo, "inventory", None) is not None:
                bot.repo.inventory.save(bot.account_id, bot.inventory)  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover - defensive path
            log.warning(f"[{self.profession_id}] immediate inventory persist failed: {exc}")

    # ── Repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return f"<{type(self).__name__} id={self.profession_id!r}>"


# ─────────────────────────────────────────────────────────────────────────────
# RequestResult
# ─────────────────────────────────────────────────────────────────────────────

class RequestResult:
    """
    Результат обробки запиту Profession-ою.
    """

    def __init__(
        self,
        *,
        approved: bool,
        reason:   str = "",
        data:     Optional[dict[str, Any]] = None,
    ) -> None:
        self.approved = approved
        self.reason   = reason
        self.data     = data or {}

    @classmethod
    def approve(
        cls,
        data:  Optional[dict[str, Any]] = None,
    ) -> "RequestResult":
        """Запит схвалено."""
        return cls(approved=True, data=data or {})

    @classmethod
    def deny(
        cls,
        reason: str,
        data:   Optional[dict[str, Any]] = None,
    ) -> "RequestResult":
        """Запит відхилено з поясненням."""
        return cls(approved=False, reason=reason, data=data or {})

    def __repr__(self) -> str:
        status = "APPROVED" if self.approved else f"DENIED({self.reason!r})"
        return f"<RequestResult {status}>"


class ProfessionFactory:
    def __init__(self) -> None:
        self._registry: Dict[str, Type[BaseProfession]] = {}

    def register(self, name: str, profession_cls: Type[BaseProfession]) -> None:
        self._registry[name] = profession_cls

    def names(self) -> List[str]:
        return list(self._registry.keys())

    def build(self, name: str) -> BaseProfession:
        profession_cls = self._registry.get(name)
        if not profession_cls:
            raise ValueError(f"Професію {name!r} не знайдено в реєстрі фабрики")
        return profession_cls()


profession_factory = ProfessionFactory()