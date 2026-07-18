"""
src/core/monitoring/account_monitors.py — контейнер моніторів одного акаунта.

AccountMonitors зберігає активні екземпляри BaseMonitor для одного account_id.
Scheduler (або SchedulerService) створює AccountMonitors при додаванні акаунта
і викликає attach/detach у потрібний момент.

Використання:
    monitors = AccountMonitors(account_id="acc_01")
    await monitors.attach_all(scheduler, ["reading", "quiz"])

    # При видаленні акаунта:
    await monitors.detach_all(scheduler)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from src.core.monitoring.monitor import BaseMonitor, monitor_registry
from src.core.logging.loggers import get_logger
from src.core.runtime.event_bus import EventBus

if TYPE_CHECKING:
    from src.core.runtime.scheduler import EventDrivenScheduler

log = get_logger("core.monitoring")


class AccountMonitors:
    """
    Контейнер активних моніторів одного акаунта.

    Зберігає прив'язку monitor_id → екземпляр BaseMonitor.
    Всі екземпляри вже «attached» — тобто підписані на події
    і мають власний стан для цього account_id.
    """

    def __init__(self, account_id: str) -> None:
        self._account_id = account_id
        self._monitors: dict[str, BaseMonitor] = {}

    # ── Attach / Detach ───────────────────────────────────────────────────────

    async def attach(
        self,
        scheduler:  "EventDrivenScheduler",
        monitor_id: str,
    ) -> Optional[BaseMonitor]:
        """
        Будує та підключає один монітор. Ідемпотентний.

        Повертає екземпляр якщо успішно, None при помилці.
        """
        if monitor_id in self._monitors:
            log.debug(
                f"[{self._account_id}] монітор {monitor_id!r} вже підключено"
            )
            return self._monitors[monitor_id]

        if monitor_id not in monitor_registry:
            log.warning(
                f"[{self._account_id}] монітор {monitor_id!r} не знайдено в реєстрі"
            )
            return None

        try:
            monitor = monitor_registry.build(monitor_id)
            await monitor.attach(scheduler, self._account_id)
            self._monitors[monitor_id] = monitor
            log.info(
                f"[{self._account_id}] монітор {monitor_id!r} підключено"
            )
            return monitor
        except Exception as exc:
            log.error(
                f"[{self._account_id}] attach монітора {monitor_id!r} провалився: {exc}",
                exc_info=True,
            )
            return None

    async def detach(
        self,
        scheduler:  "EventDrivenScheduler",
        monitor_id: str,
        bus: "EventBus"
    ) -> bool:
        """
        Відключає монітор. Ідемпотентний.

        EventBus підписки зачищаються автоматично через
        scheduler._event_bus.unsubscribe_owner(monitor).
        """
        monitor = self._monitors.pop(monitor_id, None)
        if monitor is None:
            return False

        try:
            # Знімаємо всі EventBus підписки цього монітора
            bus.unsubscribe_owner(monitor)
            await monitor.detach(scheduler, self._account_id)
            log.info(
                f"[{self._account_id}] монітор {monitor_id!r} відключено"
            )
            return True
        except Exception as exc:
            log.error(
                f"[{self._account_id}] detach монітора {monitor_id!r}: {exc}",
                exc_info=True,
            )
            return False

    async def attach_all(
        self,
        scheduler:   "EventDrivenScheduler",
        monitor_ids: list[str],
    ) -> None:
        """Підключає кілька моніторів послідовно."""
        for mid in monitor_ids:
            await self.attach(scheduler, mid)
            
    async def detach_many(
        self, 
        scheduler: "EventDrivenScheduler", 
        bus: "EventBus",
        monitor_ids: list[str],
    ) -> None:
        """Відключає кілька моніторів послідовно."""
        for mid in monitor_ids:
            await self.detach(scheduler, mid, bus)

    async def detach_all(self, scheduler: "EventDrivenScheduler", bus: "EventBus") -> None:
        """Відключає всі активні монітори."""
        for mid in list(self._monitors.keys()):
            await self.detach(scheduler, mid, bus)

    # ── Introspection ─────────────────────────────────────────────────────────

    def get(self, monitor_id: str) -> Optional[BaseMonitor]:
        return self._monitors.get(monitor_id)
    
    def monitors_ids(self) -> list[str]:
        return list(self._monitors)
    
    def active_ids(self) -> list[str]:
        return list(self._monitors.keys())

    def __repr__(self) -> str:
        return (
            f"<AccountMonitors account={self._account_id!r} "
            f"active={self.active_ids()}>"
        )
