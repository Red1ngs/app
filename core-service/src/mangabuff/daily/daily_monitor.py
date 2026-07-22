"""
daily/daily_monitor.py — DailyMonitor.

Архітектурні зміни:
    - Повністю видалено Pipeline, Step, Priority, triggers та BotWorker.
    - Розклад "коли настав новий день" переїхав у окремий сервіс
      (day-service, див. /day_service — без HTTP, лише Redis). DailyMonitor
      більше НЕ рахує сам собі, о котрій годині завтра прокинутись (жодного
      BASE_TIME/hash-jitter тут більше немає) — він лише:
        - чекає подію "daily.new_day" (прийшла з day-service через
          DayAnnouncerService.bind() → src/core/day_announcer_service.py)
          і за нею намагається зібрати бонус;
        - при невдачі сам себе перепланує через короткий retry-cooldown,
          поки не вийде;
        - як і раніше реагує на force_claim/account.unbanned.
    - DailyProfession лишається тонким виконавцем окремих HTTP-кроків
      (fetch_streak / claim_daily / claim_calendar) без побічних ефектів.
"""
from __future__ import annotations

from logging import Logger
from typing import TYPE_CHECKING, Any, Optional

from src.core.monitoring.looping_monitor import LoopingMonitor
from src.mangabuff.daily.inventory import DailyInventory
from src.utils.time import is_today

if TYPE_CHECKING:
    from src.core.core_account import Account
    from src.core.runtime.scheduler import EventDrivenScheduler

from src.core.logging.loggers import get_account_logger

# Скільки чекати перед повторною спробою, якщо попередня закінчилась
# невдало (мережева помилка, сервер відхилив запит тощо). day-service
# оповіщає лише РАЗ на день — цей retry-цикл існує саме для того, щоб
# тимчасова невдача не чекала аж до наступного "нового дня".
_RETRY_COOLDOWN_S = 300.0


class DailyMonitor(LoopingMonitor):
    """
    Монітор, який визначає ЩО робити для збору щоденних бонусів, коли
    day-service повідомив, що настав новий день (або коли прийшов
    force_claim/account.unbanned).

    Відповідальності розділені по методах (кожен метод — одна дія):
        - визначення, що саме потрібно зробити (_determine_needs)
          (_apply_streak_result / _apply_daily_result / _apply_calendar_result)
        - оркестрація одного циклу збору (_run_claim_cycle)
        - реакція на зовнішні сигнали
          (_on_new_day / _on_force_claim / _on_account_unbanned)
    """

    @property
    def monitor_id(self) -> str:
        return "daily"

    def __init__(self) -> None:
        super().__init__()
        self._last_attempt_failed: bool                     = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def attach(
        self,
        scheduler:  "EventDrivenScheduler",
        account_id: str,
    ) -> None:
        self.account_id  = account_id
        self.scheduler   = scheduler

        scheduler.subscribe("daily.new_day",     self._on_new_day)
        scheduler.subscribe("daily.force_claim", self._on_force_claim)
        scheduler.subscribe("account.unbanned",  self._on_account_unbanned)

        # Catch-up при attach(): якщо на сьогодні бонус ще не зібрано
        # (свіжий акаунт, або day-service вже надсилав "новий день" поки
        # цей акаунт/бот був офлайн) — не чекати наступної події, а
        # спробувати одразу. Якщо все вже зібрано — цикл просто нічого
        # не зробить і засне (_interval() поверне -1).
        await self._schedule_next(delay=0.0)

    async def detach(
        self,
        scheduler:  "EventDrivenScheduler",
        account_id: str,
    ) -> None:
        self._stop_loop()
        self._scheduler = None

    # ── Планування пробудження ───────────────────────────────────────────────
    #
    # DailyMonitor більше не рахує "коли завтра" — за це відповідає
    # day-service. Єдине, що лишається планувати самому — короткий retry
    # після невдалої спроби. Якщо попередня спроба була успішною (або
    # нічого робити не було), цикл засинає до наступної події.

    async def _run_cycle(self) -> None:
        await self._run_claim_cycle()

    async def _interval(self) -> float:
        return _RETRY_COOLDOWN_S if self._last_attempt_failed else -1.0

    # ── Визначення потреб ────────────────────────────────────────────────────

    @staticmethod
    def _determine_needs(
        last_daily_claimed: str | None,
        last_calendar_claimed: str | None
    ) -> tuple[bool, bool]:
        needs_daily = last_daily_claimed is None or not is_today(last_daily_claimed)
        needs_calendar = last_calendar_claimed is None or not is_today(last_calendar_claimed)
        return needs_daily, needs_calendar

    # ── Один цикл збору (оркестрація, без деталей кроків) ────────────────────

    async def _run_claim_cycle(self) -> None:
        log = self.log
        try:
            bot = self.bot

            inv: DailyInventory = bot.inventory.daily
            to_day = bot.inventory.personal.to_day
            last_calendar_claimed = inv.last_calendar_claimed
            last_daily_claimed = inv.last_daily_claimed

            needs_daily, needs_calendar = self._determine_needs(last_daily_claimed, last_calendar_claimed)

            if not needs_daily and not needs_calendar:
                self._last_attempt_failed = False
                await self._schedule_next()
                return

            failed = False
            daily_just_claimed = False

            if needs_calendar:
                # _ensure_streak_known лише встановлює inv.can_claim_calendar / inv.day
                # (доступність та номер дня стріку) — last_calendar_claimed вона не
                # чіпає, тож "чи потрібен календарний бонус сьогодні" не змінюється
                # цим кроком. Раніше тут був хибний рекомпут через is_next_day(),
                # який використовував стару семантику ("рівно наступний день") і
                # застарілу локальну змінну last_calendar_claimed — прибрано.
                failed |= await self._ensure_streak_known(log, inv)

            if needs_daily:
                daily_claim_failed = await self._claim_daily(log, inv, to_day)
                failed |= daily_claim_failed
                daily_just_claimed = not daily_claim_failed

            if needs_calendar and inv.can_claim_calendar:
                failed |= await self._claim_calendar(log, bot, inv, to_day)

            self._last_attempt_failed = failed

            # Явне збереження: RequestRouter.route() зберігає inventory одразу
            # після handle_request() профессії, тобто ДО того, як монітор допише
            # у нього результати через _apply_*_result (last_daily_claimed,
            # last_calendar_claimed, can_claim_calendar тощо). Без цього виклику
            # зміни живуть лише в пам'яті процесу до наступного випадкового
            # approved-запиту через ask(), що ненадійно.
            try:
                await self._persist_inventory(bot)
            except Exception as exc:
                log.warning(f"[DailyMonitor] Не вдалося зберегти inventory після циклу: {exc}")

            if daily_just_claimed:
                # Саме тут (а не раніше) звичайний бонус реально щойно
                # зібрано на СЬОГОДНІ — це той момент, коли для акаунта
                # можна дозволити стартувати решту процесів (mining/quiz/
                # reading вже підписані на "daily.claimed" і чекають саме
                # на нього — вони ж навмисно НЕ стартують свій перший цикл,
                # поки не побачать last_daily_claimed == сьогодні).
                await self._emit_all_claimed(bot, inv)

            await self._schedule_next()
        except ValueError as ex:
            if str(ex) == "Account не доступний":
                self._last_attempt_failed = True
                log.error("[DailyMonitor] Не вдалося отримати bot → пропуск циклу")
                await self._schedule_next()
                return

    # ── Крок: дізнатись день стріку та застосувати результат ────────────────

    async def _ensure_streak_known(self, log: "Logger", inv: "DailyInventory") -> bool:
        """Повертає True, якщо крок завершився помилкою."""
        log.info("🎁 День стріку невідомий → отримуємо календарний статус")
        res = await self.scheduler.ask(
            account_id=self._account_id,
            profession_id="daily",
            intent="fetch_streak",
            caller="daily_monitor",
        )
        if not res.approved:
            log.error(f"❌ Помилка отримання дня стріку: {res.reason}")
            return True

        self._apply_streak_result(log, inv, res.data.get("day"))
        return False

    @staticmethod
    def _apply_streak_result(log: "Logger", inv: "DailyInventory", day: Optional[int]) -> None:
        if day is None:
            log.info("🎁 Календарний бонус зараз недоступний")
            inv.can_claim_calendar = False
        else:
            log.info(f"🎁 Календар: отримано день {day}")
            inv.day                = day
            inv.can_claim_calendar = True

    # ── Крок: звичайний щоденний бонус ───────────────────────────────────────

    async def _claim_daily(self, log: "Logger", inv: "DailyInventory", to_day: str) -> bool:
        """Повертає True, якщо крок завершився помилкою/невдачею."""
        log.info("🎁 Збираємо звичайний бонус…")
        res = await self._scheduler.ask(
            account_id=self._account_id,
            profession_id="daily",
            intent="claim_daily",
            caller="daily_monitor",
        )

        if not res.approved:
            log.warning(f"⚠️ Не вдалося зібрати звичайний бонус: {res.reason}")
            return True

        return self._apply_daily_result(log, inv, to_day, res.data)
    
    @staticmethod
    def _apply_daily_result(log: "Logger", inv: "DailyInventory", to_day: str, data: dict[str, Any]) -> bool:
        if data.get("ok"):
            inv.last_daily_claimed = to_day
            log.info(f"✅ Звичайний бонус зібрано: {data.get('data')}")
            return False

        log.warning(f"⚠️ Не вдалося зібрати звичайний бонус: {data.get('data')}")
        return True

    # ── Крок: календарний бонус ──────────────────────────────────────────────

    async def _claim_calendar(
        self,
        log: "Logger",
        bot: "Account",
        inv: "DailyInventory",
        to_day: str,
    ) -> bool:
        """Повертає True, якщо крок завершився помилкою/невдачею."""
        day = inv.day
        log.info(f"🎁 Збираємо календарний бонус (день {day})…")
        res = await self._scheduler.ask(
            account_id=self._account_id,
            profession_id="daily",
            intent="claim_calendar",
            data={"day": day},
            caller="daily_monitor",
        )

        if not res.approved:
            log.warning(f"⚠️ Не вдалося зібрати календарний бонус: {res.reason}")
            self._apply_calendar_failure(inv, to_day)
            return True

        return await self._apply_calendar_result(log, bot, inv, to_day, day, res.data)

    async def _apply_calendar_result(
        self,
        log: "Logger",
        bot: "Account",
        inv: "DailyInventory",
        to_day: str,
        day: int,
        data: dict[str, Any],
    ) -> bool:
        if data.get("ok"):
            inv.last_calendar_claimed = to_day
            inv.can_claim_calendar    = False
            log.info(f"✅ Календарний бонус зібрано: {data.get('data')}")
            await self._emit_calendar_claimed(bot, day)
            return False

        log.warning(f"⚠️ Не вдалося зібрати календарний бонус: {data.get('data')}")
        self._apply_calendar_failure(inv, to_day)
        return True

    @staticmethod
    def _apply_calendar_failure(inv: "DailyInventory", to_day: str) -> None:
        # Сервер відповів, але бонус недоступний — вважаємо "зробленим" на
        # сьогодні, щоб монітор не смикав сервер знову до наступного дня.
        inv.last_calendar_claimed = to_day
        inv.can_claim_calendar    = False

    # ── Емісія подій ──────────────────────────────────────────────────────────

    async def _emit_all_claimed(self, bot: "Account", inv: "DailyInventory") -> None:
        log = self.log
        log.info("🎁 Всі бонуси на сьогодні вже зібрано")
        await self.scheduler.emit_event(
            "daily.claimed",
            {
                "account_id": bot.account_id,
                "day": inv.day,
                "last_daily_claimed": inv.last_daily_claimed,
            },
            source=bot.account_id,
        )

    async def _emit_calendar_claimed(self, bot: "Account", day: int) -> None:
        await self.scheduler.emit_event(
            "daily.calendar_claimed",
            {"account_id": bot.account_id, "day": day},
            source=bot.account_id,
        )

    # ── Реакція на day-service ────────────────────────────────────────────────

    async def _on_new_day(self, payload: dict[str, Any]) -> None:
        """
        day-service (окремий сервіс, через DayAnnouncerService) оповістив,
        що для цього акаунта настав новий день. Тут НЕ довіряємо сліпо:
        needs_daily/needs_calendar у _run_claim_cycle все одно самостійно
        звіряються з тим, що реально вже зібрано (last_daily_claimed/
        last_calendar_claimed) — тому повторна чи запізніла подія
        нешкідлива, просто нічого не зробить.
        """
        if payload.get("account_id") != self._account_id:
            return
        get_account_logger(self._account_id).info(
            f"[DailyMonitor] day-service: новий день ({payload.get('day')}) → спроба збору бонусів"
        )
        self._last_attempt_failed = False
        await self._schedule_next(delay=0.0)

    # ── Force claim ───────────────────────────────────────────────────────────

    async def _on_force_claim(self, payload: dict[str, Any]) -> None:
        if payload.get("account_id") != self._account_id:
            return
        get_account_logger(self._account_id).info(
            "[DailyMonitor] Отримано сигнал force_claim → скидання стану та позачерговий запуск"
        )
        self._reset_inventory_state()
        self._last_attempt_failed = False
        await self._schedule_next(delay=0.0)

    def _reset_inventory_state(self) -> None:
        try:
            bot = self.bot
            inv = bot.inventory.daily
            inv.last_daily_claimed    = None
            inv.last_calendar_claimed = None
            inv.can_claim_calendar    = True
        except ValueError as ex:
            if str(ex) == "Account не доступний":
                return
            
    # ── Реакція на розбан акаунта ────────────────────────────────────────────

    async def _on_account_unbanned(self, payload: dict[str, Any]) -> None:
        if payload.get("account_id") != self._account_id:
            return
        log = self.log
        log.info(
            "[DailyMonitor] Розбан отримано → позачерговий запуск"
        )
        await self._schedule_next(delay=0.0)