"""
daily/build.py — DailyProfession.

Архітектурні зміни:
    - Повністю видалено Pipeline, Step, Priority, triggers та BotWorker.
    - DailyProfession тепер виконує I/O збору бонусів напряму в асинхронному
      обробнику handle_request (інтент "claim") та зберігає стан у БД.
"""
from __future__ import annotations

from logging import Logger
from typing import TYPE_CHECKING, Any, Optional

from src.core.runtime import scheduler
from src.core.runtime.profession import BaseProfession, RequestResult
from src.mangabuff.daily.inventory import DailyInventory
from src.mangabuff.daily.stats import DailyRewardStats
from src.utils.time import is_next_day

if TYPE_CHECKING:
    from src.core.core_account import Account
    from src.core.runtime.request_router import RequestContext
    from src.core.runtime.scheduler import EventDrivenScheduler

from src.core.logging.loggers import get_account_logger


# ─────────────────────────────────────────────────────────────────────────────
# DailyProfession
# ─────────────────────────────────────────────────────────────────────────────

class DailyProfession(BaseProfession):
    """Profession «Щоденні бонуси»."""

    def __init__(self) -> None:
        self._account_id:   str                              = ""
        self._stats:        DailyRewardStats                 = DailyRewardStats()
        self._scheduler:    Optional["EventDrivenScheduler"] = None

    @property
    def profession_id(self) -> str:
        return "daily"

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def setup(self, scheduler: "EventDrivenScheduler", account_id: str) -> None:
        self._account_id = account_id
        self._scheduler  = scheduler
        scheduler.subscribe("account.unbanned", self._on_account_unbanned)

    async def restore_state(self, bot: "Account") -> None:
        inv: DailyInventory = bot.inventory.daily
        
        log = get_account_logger(self._account_id)
        log.info(
            f"DailyProfession відновлено: "
            f"daily={inv.last_daily_claimed!r} "
            f"calendar={inv.last_calendar_claimed!r} "
            f"can_claim_calendar={inv.can_claim_calendar} "
        )

    def check_guard(self, bot: "Account") -> bool:
        return not bot.inventory.personal.is_banned

    # ── handle_request ────────────────────────────────────────────────────────

    async def handle_request(
        self,
        intent: str,
        data:   dict[str, Any],
        ctx:    "RequestContext",
    ) -> RequestResult:
        log = get_account_logger(self._account_id)
        try:
            if self._scheduler is None:
                raise ValueError("Scheduler не доступний")

            bot = self._scheduler.get_bot(ctx.account_id)
            if bot is None:
                raise ValueError("bot != None")

            if not self.check_guard(bot):
                return RequestResult.deny("account is banned")

            if intent == "fetch_streak":
                return await self._handle_fetch_streak(log, bot)
            if intent == "claim_daily":
                return await self._handle_claim_daily(log, bot)
            if intent == "claim_calendar":
                return await self._handle_claim_calendar(log, bot, data)
            if intent == "get_status":
                return await self._handle_get_status(log, bot)
            return RequestResult.deny(f"unknown intent: {intent!r}")
        except Exception as ex:
            return RequestResult.deny(str(ex))

    async def _handle_fetch_streak(self, log: "Logger", bot: "Account") -> RequestResult:
        """Лише запит до сервера. Не чіпає inventory, не емітить подій."""
        daily_cfg = bot.app_config.daily
        try:
            result = await bot.safe_session.fetch_daily_streak(daily_cfg)
            return RequestResult.approve(data={"day": result.data})
        except Exception as e:
            log.error(f"❌ Помилка отримання дня стріку: {e}", exc_info=True)
            return RequestResult.deny(f"parse_error: {e}")

    async def _handle_claim_daily(self, log: "Logger", bot: "Account") -> RequestResult:
        """Лише запит claim_daily. Повертає ok/data, нічого не зберігає."""
        daily_cfg = bot.app_config.daily
        personal_cfg = bot.app_config.personal
        try:
            result = await bot.safe_session.claim_daily(daily_cfg, personal_cfg)
            self._stats.daily_results = result.data or {}
            return RequestResult.approve(data={"ok": result.ok, "data": result.data or {}})
        except Exception as e:
            log.error(f"❌ Помилка під час збору звичайного бонусу: {e}", exc_info=True)
            return RequestResult.deny(str(e))

    async def _handle_claim_calendar(self, log: "Logger", bot: "Account", data: dict[str, Any]) -> RequestResult:
        """Лише запит claim_calendar(day). day передається монітором явно."""
        day = data.get("day")
        if day is None:
            return RequestResult.deny("day is required")

        daily_cfg = bot.app_config.daily
        try:
            result = await bot.safe_session.claim_calendar(day, daily_cfg)
            self._stats.calendar_results = result.data or {}
            return RequestResult.approve(data={"ok": result.ok, "data": result.data or {}, "day": day})
        except Exception as e:
            log.error(f"❌ Помилка під час збору календарного бонусу: {e}", exc_info=True)
            return RequestResult.deny(str(e))

    async def _handle_get_status(self, log: "Logger", bot: "Account") -> RequestResult:
        inv: DailyInventory = bot.inventory.daily
        personal = bot.inventory.personal
        to_day = personal.to_day
        return RequestResult.approve(data={
            "last_daily_claimed":    inv.last_daily_claimed,
            "last_calendar_claimed": inv.last_calendar_claimed,
            "daily_done":            inv.last_daily_claimed    == to_day,
            "calendar_done":         inv.last_calendar_claimed == to_day,
            "calendar_day":          inv.day,
            "can_claim_calendar":    inv.can_claim_calendar,
            "stats":                 self._stats.data,
        })
        
    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_account_unbanned(self, payload: dict[str, Any]) -> None:
        if payload.get("account_id") != self._account_id:
            return
        get_account_logger(self._account_id).info("DailyProfession: розбан отримано, сповіщаємо монітор")
        if self._scheduler is not None:
            await self._scheduler.emit_event("daily.force_claim", {"account_id": self._account_id}, source=self._account_id)