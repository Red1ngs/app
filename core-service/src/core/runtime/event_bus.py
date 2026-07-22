"""src/core/runtime/event_bus.py — async pub/sub EventBus."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Awaitable, Callable

from src.core.logging.loggers import get_logger
log = get_logger("runtime.event_bus")

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[EventCallback]] = defaultdict(list)

    def subscribe(self, event_name: str, callback: EventCallback) -> None:
        if callback not in self._subs[event_name]:
            self._subs[event_name].append(callback)
            log.debug(f"[EventBus] subscribe {event_name!r} → {callback.__qualname__}")

    def unsubscribe(self, event_name: str, callback: EventCallback) -> None:
        try:
            self._subs[event_name].remove(callback)
        except ValueError:
            pass

    def unsubscribe_all(self, callback: EventCallback) -> None:
        for listeners in self._subs.values():
            try:
                listeners.remove(callback)
            except ValueError:
                pass
            
    def unsubscribe_owner(self, owner: object) -> None:
        """Видаляє всі callbacks що належать owner (за __self__)."""
        for listeners in self._subs.values():
            listeners[:] = [
                cb for cb in listeners
                if getattr(cb, "__self__", None) is not owner
            ]

    async def emit(
        self,
        event_name: str,
        payload:    dict[str, Any],
        *,
        source: str = "system",
    ) -> int:
        listeners = list(self._subs.get(event_name, []))
        if not listeners:
            return 0
        enriched = {**payload, "_event": event_name, "_source": source}
        log.debug(f"[EventBus] emit {event_name!r} → {len(listeners)} subscribers")
        results = await asyncio.gather(
            # FIX Bug 11: перейменовано _safe_call → _call_subscriber,
            # щоб назва відповідала реальній поведінці (логує + re-raise).
            *(self._call_subscriber(cb, enriched) for cb in listeners),
            return_exceptions=True,
        )
        errors = sum(1 for r in results if isinstance(r, Exception))
        return len(listeners) - errors

    async def _call_subscriber(self, cb: EventCallback, payload: dict[str, Any]) -> None:
        """
        Викликає callback. При помилці — логує і прокидає далі (в gather
        з return_exceptions=True), щоб помилка одного підписника не заважала
        іншим. Назва відображає реальну поведінку: НЕ «safe» (не поглинає
        помилки), а «call subscriber».
        """
        try:
            await cb(payload)
        except Exception as e:
            log.error(f"[EventBus] {cb.__qualname__!r} raised: {e}", exc_info=True)
            raise