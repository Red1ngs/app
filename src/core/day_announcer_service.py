"""
src/core/day_announcer_service.py — DayAnnouncerService (CoreService).

Міст між day-service (окремий сервіс, тільки Redis, без HTTP — див.
/day_service) і локальним EventBus, на який підписаний DailyMonitor.

Побудований за ТИМ САМИМ патерном, що SocketService
(src/mangabuff/session/socket/socket_service.py) для account_event_bus:
    bind(bot)   — реєструє акаунт у day-service (fire-and-forget) і
                  підписує day_event_bus на "new_day" для цього акаунта
    unbind()    — відписується і знімає реєстрацію

На відміну від SocketService (який ретранслює в bot.event_bus),
DailyMonitor підписується не на bot.event_bus, а на ГЛОБАЛЬНИЙ
scheduler-bus (scheduler.subscribe("daily.force_claim"/"account.unbanned"))
— так само подія "новий день" ретранслюється саме туди
(EventDrivenScheduler.get_instance().emit_event(...)), а не в bot.event_bus,
щоб DailyMonitor.attach() не довелось міняти патерн підписки заради
одного джерела подій.

Реєстрація (mangabuff/setup.py):
    profession_registry.add_core_service(DayAnnouncerService)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core.day_client import day_client
from src.core.day_events import day_event_bus
from src.core.logging.loggers import get_logger
from src.core.runtime.core_service import CoreService

if TYPE_CHECKING:
    from src.core.core_account import Account

log = get_logger("service.day_announcer")


class DayAnnouncerService(CoreService):
    """
    Реєструє акаунт у day-service і перекидає його подію "новий день"
    у глобальний scheduler-bus як "daily.new_day" — саме на цю подію
    підписаний DailyMonitor (замість того, щоб самому рахувати, коли
    завтра прокинутись).
    """

    def __init__(self) -> None:
        self._account: "Account | None" = None
        self._forwarder = None

    @property
    def service_id(self) -> str:
        return "day_announcer"

    async def bind(self, bot: "Account") -> None:
        self._account = bot

        # Fire-and-forget: day-service тимчасово недоступний — деградація
        # (акаунт не отримає майбутніх "будильників", поки сервіс не
        # підніметься), а не привід валити add_account(). DailyMonitor і
        # так підстраховується catch-up-перевіркою при своєму attach().
        try:
            await day_client.register(bot.account_id)
        except Exception as e:
            log.warning(f"[{bot.account_id}] day-service register не вдався: {e}")

        forwarder = self._make_forwarder(bot)
        self._forwarder = forwarder
        day_event_bus.subscribe(bot.account_id, "new_day", forwarder)
        log.info(f"[{bot.account_id}] DayAnnouncerService: підписано на new_day (redis)")

    async def unbind(self) -> None:
        if self._account is not None and self._forwarder is not None:
            day_event_bus.unsubscribe(self._account.account_id, "new_day", self._forwarder)
            await day_client.unregister(self._account.account_id)
        self._forwarder = None
        self._account = None

    # Лишені як no-op заради сумісності з рештою CoreService-lifecycle —
    # так само, як у SocketService: підписка на day-service не залежить
    # від наявності живої сесії акаунта.
    async def on_session_ready(self, bot: "Account") -> None:
        pass

    async def on_session_closing(self, bot: "Account") -> None:
        pass

    # ── Private ───────────────────────────────────────────────────────────────

    def _make_forwarder(self, bot: "Account"):
        async def forwarder(data: dict[str, Any]) -> None:
            from src.core.runtime.scheduler import EventDrivenScheduler

            log.info(f"[{bot.account_id}] day-service: новий день ({data.get('day')})")
            try:
                scheduler = EventDrivenScheduler.get_instance()
            except RuntimeError:
                # Скедулер ще/вже не піднятий (тести, рання ініціалізація) —
                # нема куди емітити, тихо виходимо.
                return
            await scheduler.emit_event(
                "daily.new_day",
                {"account_id": bot.account_id, "day": data.get("day")},
                source="day_service",
            )

        return forwarder
