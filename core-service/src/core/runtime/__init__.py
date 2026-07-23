"""src/core/runtime — event-driven runtime layer.

EventDrivenScheduler користується RedisEventBus (redis-service-client) —
розподіленим Redis pub/sub транспортом — для subscribe()/emit_event():
події professions/monitors бачить весь кластер процесів/подів, а не
лише той, що їх опублікував.

Колишній in-process EventBus (src/core/runtime/event_bus.py) видалено
повністю разом з Account.event_bus — усі події (включно з socket-подіями,
які раніше йшли в персональну шину акаунта через SocketService) тепер
йдуть виключно через цей глобальний scheduler-bus, з фільтрацією по
account_id у payload на боці підписника.
"""
from redis_service_client import RedisEventBus, RedisLock, EventCallback

from src.core.runtime.profession import BaseProfession, RequestResult
from src.core.runtime.request_router import RequestContext, RequestRouter
from src.core.runtime.scheduler import EventDrivenScheduler, AccountContainer

__all__ = [
    "EventDrivenScheduler",
    "AccountContainer",
    "RedisEventBus",
    "RedisLock",
    "EventCallback",
    "BaseProfession",
    "RequestResult",
    "RequestContext",
    "RequestRouter",
]