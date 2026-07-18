"""
src/core/monitoring/monitor.py — BaseMonitor + MonitorRegistry.

Архітектура моніторингу:
    Monitor — самостійний модуль спостереження що:
        • підписується на події EventBus
        • веде власний стан (у пам'яті або через inventory)
        • надсилає ask() до profession коли треба щось зробити

    MonitorRegistry — реєстр всіх моніторів за іменем.
        Монітори реєструються один раз, потім підключаються
        до конкретних акаунтів через attach/detach.

Відмінність від Profession:
    Profession   — «виконавець»: отримує задачу і робить IO.
    Monitor      — «спостерігач»: вирішує КОЛИ і ЯК викликати виконавця.

    Profession НЕ знає про таймінги → цим займається Monitor.
    Monitor    НЕ робить IO напряму  → делегує через ask().

Lifecycle:
    attach(scheduler, account_id)   — підписки + старт внутрішнього стану
    detach(scheduler, account_id)   — відписки + зупинка

Приклад:
    class ReadingMonitor(BaseMonitor):
        monitor_id = "reading"

        async def attach(self, scheduler, account_id):
            scheduler.subscribe("loader.chapters_ready", self._on_chapters_ready)
            self._schedule_next(scheduler, account_id)

        async def _on_chapters_ready(self, payload):
            ...  # надсилаємо ask reader зробити читання
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Dict, List, Type

if TYPE_CHECKING:
    from src.core.runtime.scheduler import EventDrivenScheduler

from src.core.core_account import Account
from src.core.logging.loggers import get_logger

log = get_logger("core.monitoring")


# ─────────────────────────────────────────────────────────────────────────────
# BaseMonitor
# ─────────────────────────────────────────────────────────────────────────────

class BaseMonitor(ABC):
    """
    Базовий клас монітора.

    Монітор підписується на події та/або веде власний цикл і вирішує
    КОЛИ треба щось зробити — після чого надсилає ask() до profession.

    Один екземпляр монітора створюється на акаунт (через MonitorRegistry.build).
    Підкласи НЕ повинні:
        - виконувати IO напряму
        - зберігати посилання на bot/BotWorker
        - викликати profession напряму — тільки через scheduler.ask()
    """

    @property
    @abstractmethod
    def monitor_id(self) -> str:
        """Унікальний рядковий ідентифікатор. Наприклад: 'reading', 'quiz'."""
        ...

    @abstractmethod
    async def attach(
        self,
        scheduler:  "EventDrivenScheduler",
        account_id: str,
    ) -> None:
        """
        Підключає монітор до акаунта.

        Тут монітор:
          - підписується на події: scheduler.subscribe(...)
          - зберігає account_id та посилання на scheduler
          - ініціалізує внутрішній стан

        НЕ робить IO тут — лише реєстрація та ініціалізація.
        """
        ...

    @abstractmethod
    async def detach(
        self,
        scheduler:  "EventDrivenScheduler",
        account_id: str,
    ) -> None:
        """
        Відключає монітор від акаунта.

        Тут монітор:
          - скасовує заплановані wake-up (якщо є)
          - звільняє ресурси

        EventBus відписки відбуваються автоматично через
        scheduler.unsubscribe_owner(self) — не потрібно робити вручну.
        """
        ...
        
    async def _persist_inventory(self, bot: "Account") -> None:
        """Негайне асинхронне збереження інвентарю."""
        try:
            if bot.repo and bot.repo.inventory:
               bot.repo.inventory.save(bot.account_id, bot.inventory)
        except Exception as exc:
            get_logger("core.monitoring").warning(
                f"[{bot.account_id}] immediate inventory persist failed: {exc}"
            )


    def __repr__(self) -> str:
        return f"<{type(self).__name__} id={self.monitor_id!r}>"


# ─────────────────────────────────────────────────────────────────────────────
# MonitorRegistry
# ─────────────────────────────────────────────────────────────────────────────

class MonitorRegistry:
    """
    Реєстр класів моніторів.

    Зберігає Type[BaseMonitor], а не екземпляри —
    кожен attach створює свіжий екземпляр для конкретного акаунта.

    Використання:
        monitor_registry.register("reading", ReadingMonitor)

        # При додаванні акаунта:
        monitor = monitor_registry.build("reading")
        await monitor.attach(scheduler, account_id)
    """

    def __init__(self) -> None:
        self._registry: Dict[str, Type[BaseMonitor]] = {}

    def register(self, name: str, monitor_cls: Type[BaseMonitor]) -> None:
        """Реєструє клас монітора під іменем."""
        if name in self._registry:
            log.warning(f"[MonitorRegistry] перезапис монітора {name!r}")
        self._registry[name] = monitor_cls
        log.debug(f"[MonitorRegistry] зареєстровано {name!r} → {monitor_cls.__name__}")

    def build(self, name: str) -> BaseMonitor:
        """Створює новий екземпляр монітора. Raises ValueError якщо не знайдено."""
        cls = self._registry.get(name)
        if cls is None:
            raise ValueError(f"Монітор {name!r} не знайдено в реєстрі")
        return cls()

    def names(self) -> List[str]:
        """Список зареєстрованих імен моніторів."""
        return list(self._registry.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._registry

    def __repr__(self) -> str:
        return f"<MonitorRegistry monitors={self.names()}>"


# Глобальний синглтон реєстру
monitor_registry = MonitorRegistry()
