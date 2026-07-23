"""
src/mangabuff/session/socket/socket_service.py — SocketService (CoreService).

Роль лишилась та сама: міст між "сирими" socket-подіями акаунта і
ГЛОБАЛЬНИМ scheduler-bus (Redis), на який Profession підписується через
scheduler.subscribe(). Раніше ретрансляція йшла в персональний
bot.event_bus (in-process, окремий на кожен акаунт) — тепер, за тим
самим патерном, що DayAnnouncerService для "daily.new_day", подія летить
одразу в EventDrivenScheduler.emit_event(...) з account_id у payload;
підписник сам фільтрує `payload.get("account_id") == self._account_id`
(як це вже роблять ReadingMonitor тощо для інших глобальних подій).

Що змінилось: BotSocket (транспорт wss10) тепер живе в account-service.
Замість `session.socket.on(event, forwarder)` тут підписка йде через
AccountEventBus (Redis pub/sub) — account_event_bus.subscribe(account_id,
event, forwarder). Мапа _SOCKET_TO_BUS і вся бізнес-семантика подій
лишається тут, у бізнес-сервісі — account-service про неї нічого не знає,
він просто ретранслює сирі socket-події в Redis.

Підписуємось у bind(), а не в on_session_ready(): на відміну від старої
версії, тут не потрібна жива сесія в момент підписки — Redis-підписка не
залежить від того, підключений акаунт зараз чи ні (account-service почне
публікувати події одразу як тільки акаунт підключиться і відкриє сокет).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core.account_events import account_event_bus
from src.core.runtime.core_service import CoreService
from src.core.logging.loggers import get_logger

if TYPE_CHECKING:
    from src.core.core_account import Account

log = get_logger("service.socket")


# Мапа: socket-подія → event_bus-подія.
# Додати новий рядок тут — і Profession вже може на неї підписатись.
_SOCKET_TO_BUS: dict[str, str] = {
    "new-notify":              "socket.notify",
    "new-AchievementUnlocked": "socket.achievement",
    "new-sendNewTrade":        "socket.trade_received",
    "auction_bid":             "socket.auction_bid",
    "newLevel":                "socket.level_up",
    "new-sendNewPack":         "socket.pack_received",
    "new-message":             "socket.message",
    "match_found":             "socket.match_found",
}


class SocketService(CoreService):
    """
    Прослуховує (через Redis) socket-події конкретного акаунта і
    ретранслює їх у глобальний scheduler-bus.

    Lifecycle:
        bind(bot)    — підписується на всі socket-події з мапи (Redis)
        unbind()     — відписується
    """

    def __init__(self) -> None:
        self._account: "Account | None" = None
        self._forwarders: dict[str, Any] = {}

    @property
    def service_id(self) -> str:
        return "socket"

    async def bind(self, bot: "Account") -> None:
        self._account = bot
        for socket_event, bus_event in _SOCKET_TO_BUS.items():
            forwarder = self._make_forwarder(bot, bus_event)
            self._forwarders[socket_event] = forwarder
            account_event_bus.subscribe(bot.account_id, socket_event, forwarder)
        log.info(f"[{bot.account_id}] SocketService: підписано на {len(_SOCKET_TO_BUS)} подій (redis)")

    async def unbind(self) -> None:
        if self._account is not None:
            for socket_event, forwarder in self._forwarders.items():
                account_event_bus.unsubscribe(self._account.account_id, socket_event, forwarder)
        self._forwarders.clear()
        self._account = None

    # Лишені як no-op заради сумісності з рештою CoreService-lifecycle —
    # підписка більше не прив'язана до наявності живої сесії.
    async def on_session_ready(self, bot: "Account") -> None:
        pass

    async def on_session_closing(self, bot: "Account") -> None:
        pass

    # ── Private ───────────────────────────────────────────────────────────────

    def _make_forwarder(self, bot: "Account", bus_event: str):
        async def forwarder(data: Any) -> None:
            from src.core.runtime.scheduler import EventDrivenScheduler

            payload = data if isinstance(data, dict) else ({"raw": data} if data is not None else {})
            payload = {**payload, "account_id": bot.account_id}

            log.debug(f"[{bot.account_id}] socket → bus [{bus_event}]: {payload}")
            try:
                scheduler = EventDrivenScheduler.get_instance()
            except RuntimeError:
                # Скедулер ще/вже не піднятий (тести, рання ініціалізація) —
                # нема куди емітити, тихо виходимо.
                return
            try:
                await scheduler.emit_event(bus_event, payload, source="socket")
            except Exception as e:
                log.error(f"[{bot.account_id}] emit_event [{bus_event}] failed: {e}")

        return forwarder
