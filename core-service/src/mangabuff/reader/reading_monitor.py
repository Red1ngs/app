"""
mangabuff/reader/reading_monitor.py — ReadingMonitor.

Відповідальність:
    КОЛИ читати і З ЯКИМИ параметрами.
    ЩО САМЕ робити — в ReaderProfession через scheduler.ask().

Правила монітора:
    • НЕ мутує inventory напряму.
    • НЕ викликає repo.inventory.save().
    • Усі зміни стану — через scheduler.ask() → RequestRouter → auto-save.

Потік при reward:
    do_read → emit reader.reward_received
           → _on_reward_received → ask("reader", "account_reward")
           → ReaderProfession._handle_account_reward оновлює inventory
           → RequestRouter auto-save
           → result.data → монітор вирішує emit slot_limit_reached

Логіка читання:
    1. attach  → _schedule_next(delay=0).
    2. loader.chapters_ready   → достроковий ask.
    3. daily.claimed           → reset флагів + новий ask.
    4. reader.chapters_exhausted → sleeping до chapters_ready.
    5. reader.slot_limit_reached → наступний слот або зупинка до daily.claimed.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from logging import Logger
from typing import TYPE_CHECKING, Any, Optional

from src.core.monitoring.looping_monitor import LoopingMonitor
from src.utils.time import is_today

if TYPE_CHECKING:
    from src.core.runtime.scheduler import EventDrivenScheduler
    from src.core.config.app import RewardSlotCfg


# ─────────────────────────────────────────────────────────────────────────────
# ReadingParams
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ReadingParams:
    limit:        int                  = 2
    include_tags: Optional[list[str]] = None
    exclude_tags: Optional[list[str]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "limit":        self.limit,
            "include_tags": self.include_tags,
            "exclude_tags": self.exclude_tags,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ReadingParams":
        return cls(
            limit        = int(d.get("limit", 2)),
            include_tags = d.get("include_tags") or None,
            exclude_tags = d.get("exclude_tags") or None,
        )


# ─────────────────────────────────────────────────────────────────────────────
# ReadingMonitor
# ─────────────────────────────────────────────────────────────────────────────

class ReadingMonitor(LoopingMonitor):

    @property
    def monitor_id(self) -> str:
        return "reading"

    def __init__(self) -> None:
        super().__init__()
        self._sleeping:           bool                          = False
        self._slot_limit_reached: bool                          = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def attach(self, scheduler: "EventDrivenScheduler", account_id: str) -> None:
        self.account_id = account_id
        self.scheduler  = scheduler

        scheduler.subscribe("loader.chapters_ready",     self._on_chapters_ready)
        scheduler.subscribe("daily.claimed",             self._on_daily_claimed)
        scheduler.subscribe("reader.chapters_exhausted", self._on_chapters_exhausted)
        scheduler.subscribe("reader.chapters_marked",    self._on_chapters_marked)
        scheduler.subscribe("reader.reward_received",    self._on_reward_received)
        scheduler.subscribe("reader.slot_limit_reached", self._on_slot_limit_reached)

        await self._schedule_next(delay=0.0)

    async def detach(self, scheduler: "EventDrivenScheduler", account_id: str) -> None:
        self._stop_loop()
        self._scheduler = None

    # ── Scheduling ────────────────────────────────────────────────────────────
    #
    # Власне планування (delay/скасування/try-except) винесено у
    # LoopingMonitor. Тут лишається тільки те, що специфічне для reading:
    # яка дія відбувається на пробудженні і яка затримка до нього.

    async def _run_cycle(self) -> None:
        await self._send_ask()

    def _loop_logger(self) -> Logger:
        return self.log

    async def _interval(self) -> float:
        """Інтервал для поточного слота. -1.0 = всі слоти вичерпані."""
        cfg  = self.bot.app_config.reader
        inv  = self.bot.inventory.reader
        
        mode_name = inv.active_mode or cfg.default_mode
        mode = cfg.get_mode(mode_name)

        if not mode.slots:
            return mode.fallback_interval_s

        slot = cfg.next_available_slot_for_mode(
            mode_name, inv.slot_counts, inv.slot_chapters_spent
        )
        if slot is not None:
            return slot.interval_seconds

        # Всі слоти вичерпані — emit і сигналізуємо «не планувати»
        self.log.info(
            f"[ReadingMonitor] всі слоти mode={mode_name!r} вичерпано "
            f"→ emit reader.slot_limit_reached"
        )
        asyncio.ensure_future(self.scheduler.emit_event(
            "reader.slot_limit_reached",
            {"account_id": self._account_id, "mode": mode_name, "slot": None},
            source=self.account_id,
        ))
        return -1.0

    def _active_slot(self) -> Optional["RewardSlotCfg"]:
        cfg = self.bot.app_config.reader
        inv = self.bot.inventory.reader
        mode_name = inv.active_mode or cfg.default_mode
        return cfg.next_available_slot_for_mode(
            mode_name, inv.slot_counts, inv.slot_chapters_spent
        )

    # ── Daily guard ───────────────────────────────────────────────────────────

    def _waiting_for_daily(self) -> bool:
        scheduler = self.scheduler
        
        if not scheduler.has_profession(self.account_id, "daily"):
            return False
        
        daily = self.bot.inventory.daily
        
        # Перевіряємо, чи настав новий день відносно останнього збору щоденного бонусу.
        result = is_today(daily.last_daily_claimed)
        return result

    # ── Ask ───────────────────────────────────────────────────────────────────

    async def _send_ask(self) -> None:
        """
        Надсилає ask("reader", "do_read").
        Після відповіді — якщо не було reward — перевіряє ліміт по главах
        з result.data (inventory вже збережено RequestRouter).
        """

        if self._sleeping:
            self.log.debug("[ReadingMonitor] sleeping — пропускаємо ask")
            return
        
        if self._slot_limit_reached:
            self.log.info("[ReadingMonitor] slot limit — чекаємо daily.claimed")
            return
        
        if not self._waiting_for_daily():
            self.log.info("[ReadingMonitor] daily ще не зібрано → чекаємо daily.claimed")
            return

        params      = self._reading_params()
        active_slot = self._active_slot()

        self.log.info(
            f"[ReadingMonitor] → ask reader do_read "
            f"mode={self._active_mode_name()!r} "
            f"limit={params.limit} "
            f"include={params.include_tags} exclude={params.exclude_tags}"
        )

        ask_data = params.to_dict()
        ask_data["active_slot"] = active_slot.name if active_slot else None

        result = await self.scheduler.ask(
            account_id    = self.account_id,
            profession_id = "reader",
            intent        = "do_read",
            data          = ask_data,
            caller        = "reading_monitor",
        )

        if not result.approved:
            self.log.warning(f"[ReadingMonitor] do_read відхилено: {result.reason}")
            if not self._sleeping and not self._slot_limit_reached:
                await self._schedule_next()
            return

        data = result.data or {}

        # Якщо reward — ліміт перевірить _on_reward_received після ask("account_reward")
        if data.get("reward") or active_slot is None:
            if not self._sleeping and not self._slot_limit_reached:
                await self._schedule_next()
            return

        # Без reward — перевіряємо ліміт по главах з result.data
        spent_new = data.get("slot_chapters_spent")
        if spent_new is not None:
            cap = active_slot.max_chapters_per_slot
            self.log.info(
                f"[ReadingMonitor] slot={active_slot.name!r} "
                f"chapters_spent={spent_new}"
                + (f"/{cap}" if cap > 0 else "")
            )
            if cap > 0 and spent_new >= cap:
                self.log.info(
                    f"[ReadingMonitor] slot={active_slot.name!r} вичерпано по главах "
                    f"({spent_new}/{cap}) → emit reader.slot_limit_reached"
                )
                await self.scheduler.emit_event(
                    "reader.slot_limit_reached",
                    {
                        "account_id":  self._account_id,
                        "slot":        active_slot.name,
                        "daily_limit": active_slot.daily_limit,
                    },
                    source=self.account_id,
                )
                return  # _on_slot_limit_reached вирішить що далі

        if not self._sleeping and not self._slot_limit_reached:
            await self._schedule_next()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _reading_params(self) -> ReadingParams:
        inv = getattr(self.bot.inventory, "reader", None)
        if inv is None:
            return ReadingParams()
        raw = inv.data.get("reading_params")
        return ReadingParams.from_dict(raw) if raw else ReadingParams()

    def _active_mode_name(self) -> str:
        cfg = self.bot.app_config.reader
        inv = self.bot.inventory.reader
        return inv.active_mode or cfg.default_mode

    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_chapters_ready(self, payload: dict[str, Any]) -> None:
        if payload.get("account_id") != self._account_id:
            return
        if self._sleeping:
            self.log.info("[ReadingMonitor] loader.chapters_ready → виходимо зі sleeping")
            self._sleeping = False
        else:
            self.log.info("[ReadingMonitor] loader.chapters_ready → достроковий ask")
        await self._schedule_next(delay=0.0)

    async def _on_daily_claimed(self, payload: dict[str, Any]) -> None:
        if payload.get("account_id") != self._account_id:
            return
        self.log.info("[ReadingMonitor] daily.claimed → виходимо зі sleeping")
        self._sleeping           = False
        self._slot_limit_reached = False
        delay = max(await self._interval(), 0.0)
        self.log.info(f"[ReadingMonitor] daily.claimed → наступний ask через {delay:.0f}s")
        await self._schedule_next(delay=delay)

    async def _on_chapters_exhausted(self, payload: dict[str, Any]) -> None:
        if payload.get("account_id") != self._account_id:
            return
        self.log.info(
            "[ReadingMonitor] reader.chapters_exhausted → sleeping"
        )
        self._sleeping = True
        self._cancel_wakeup()

    async def _on_slot_limit_reached(self, payload: dict[str, Any]) -> None:
        if payload.get("account_id") != self._account_id:
            return
        next_slot = self._active_slot()
        if next_slot is not None:
            self.log.info(
                f"[ReadingMonitor] slot={payload.get('slot')!r} вичерпано "
                f"→ переходимо на slot={next_slot.name!r}"
            )
            await self._schedule_next()
        else:
            self.log.info(
                f"[ReadingMonitor] slot={payload.get('slot')!r} вичерпано, "
                f"всі слоти закриті → зупиняємо до daily.claimed"
            )
            self._slot_limit_reached = True
            self._cancel_wakeup()

    async def _on_reward_received(self, payload: dict[str, Any]) -> None:
        """
        Монітор НЕ мутує inventory. Делегує в ReaderProfession через ask("account_reward").
        RequestRouter зробить auto-save після approve.
        Після отримання result.data — вирішує чи потрібен emit slot_limit_reached.
        """
        if payload.get("account_id") != self._account_id:
            return

        result = await self.scheduler.ask(
            account_id    = self.account_id,
            profession_id = "reader",
            intent        = "account_reward",
            data          = {
                "reward":        payload.get("reward", {}),
                "chapters_read": payload.get("chapters_read", 0),
                "active_slot":   payload.get("active_slot"),
            },
            caller = "reading_monitor",
        )
        
        if result.data.get("token", False):
            await self.scheduler.ask(
                account_id    = self.account_id,
                profession_id = "reader",
                intent        = "claim_candy",
                data          = {
                    "token":    result.data["token"],
                },
                caller = "reading_monitor",
            )

        if not result.approved:
            self.log.warning(f"[ReadingMonitor] account_reward відхилено: {result.reason}")
            if not self._sleeping and not self._slot_limit_reached:
                await self._schedule_next()
            return

        data       = result.data or {}
        slot_name  = data.get("slot")
        new_count  = data.get("new_count", 0)
        new_spent  = data.get("new_spent", 0)
        cap        = data.get("cap", 0)
        daily_lim  = data.get("daily_limit", 0)

        if slot_name is None:
            if not self._sleeping and not self._slot_limit_reached:
                await self._schedule_next()
            return

        # Перевіряємо ліміт по главах
        if cap > 0 and new_spent >= cap:
            self.log.info(
                f"[ReadingMonitor] slot={slot_name!r} вичерпано по главах "
                f"({new_spent}/{cap}) → emit reader.slot_limit_reached"
            )
            await self.scheduler.emit_event(
                "reader.slot_limit_reached",
                {"account_id": self.account_id, "slot": slot_name,
                 "count": new_count, "daily_limit": daily_lim},
                source=self.account_id,
            )
            return

        # Перевіряємо ліміт по нагородах
        if new_count >= daily_lim:
            self.log.info(
                f"[ReadingMonitor] slot={slot_name!r} досяг ліміту "
                f"({new_count}/{daily_lim}) → emit reader.slot_limit_reached"
            )
            await self.scheduler.emit_event(
                "reader.slot_limit_reached",
                {"account_id": self.account_id, "slot": slot_name,
                 "count": new_count, "daily_limit": daily_lim},
                source=self.account_id,
            )
            return

        if not self._sleeping and not self._slot_limit_reached:
            await self._schedule_next()
            
    async def _on_chapters_marked(self, payload: dict[str, Any]) -> None:
        if payload.get("account_id") != self._account_id:
            return
        self.log.info(
            f"[ReadingMonitor] reader.chapters_marked ({payload.get('marked')} глав) "
            f"→ достроковий ask"
        )
        # Адмін вручну позначив глави прочитаними — стан черги змінився,
        # має сенс перевірити чергу негайно, а не чекати старого таймера
        # чи застряглого sleeping/slot_limit.
        self._sleeping           = False
        self._slot_limit_reached = False
        await self._schedule_next(delay=0.0)