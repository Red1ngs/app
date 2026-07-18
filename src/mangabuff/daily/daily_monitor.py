"""
daily/daily_monitor.py — DailyMonitor.

Архітектурні зміни:
    - Повністю видалено Pipeline, Step, Priority, triggers та BotWorker.
    - DailyMonitor тепер керує не лише розкладом, а й усіма рішеннями:
      яку дію викликати, як оновлювати inventory, коли емітити події.
    - DailyProfession лишається тонким виконавцем окремих HTTP-кроків
      (fetch_streak / claim_daily / claim_calendar) без побічних ефектів.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
from logging import Logger
from typing import TYPE_CHECKING, Any, Optional

from src.core.monitoring.looping_monitor import LoopingMonitor
from src.mangabuff.daily.inventory import DailyInventory
from src.utils.time import _parse_hh_mm, is_equal, is_today, now, seconds_until_tomorrow_time_stable

if TYPE_CHECKING:
    from src.core.core_account import Account
    from src.core.runtime.scheduler import EventDrivenScheduler

from src.core.logging.loggers import get_account_logger


class DailyMonitor(LoopingMonitor):
    """
    Монітор, який визначає КОЛИ і ЩО робити для збору щоденних бонусів.

    Відповідальності розділені по методах (кожен метод — одна дія):
        - планування часу пробудження (_schedule_next / _calculate_delay)
        - визначення, що саме потрібно зробити (_determine_needs)
          (_apply_streak_result / _apply_daily_result / _apply_calendar_result)
        - оркестрація одного циклу збору (_run_claim_cycle)
        - реакція на зовнішні сигнали (_on_force_claim / _on_account_unbanned)
    """

    BASE_TIME = "04:30"

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

        scheduler.subscribe("daily.force_claim", self._on_force_claim)
        scheduler.subscribe("account.unbanned", self._on_account_unbanned)

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
    # Власне планування (delay/скасування/try-except) винесено у
    # LoopingMonitor. Тут лишається тільки те, що специфічне для daily:
    # звідки брати затримку, коли її не передано явно, і що саме робити
    # прокинувшись.

    async def _run_cycle(self) -> None:
        await self._run_claim_cycle()

    async def _interval(self) -> float:
        return self._calculate_delay()

    def _get_scheduled_time_str(self, base: str, account_id: str, jitter_minutes: int) -> str:
        """
        Розраховує точний стабільний час запуску (HH:MM) для акаунта 
        з урахуванням симетричного зсуву (jitter).
        """
        h, m = _parse_hh_mm(base)
        hash_val = int(hashlib.md5(account_id.encode()).hexdigest(), 16)
        max_range = jitter_minutes * 2
        jitter_offset_minutes = (hash_val % (max_range + 1)) - jitter_minutes
        
        total_minutes = (h * 60 + m + jitter_offset_minutes) % 1440
        new_h, new_m = divmod(total_minutes, 60)
        return f"{new_h:02d}:{new_m:02d}"

    def _calculate_delay(self) -> float:
        log = self.log
        try: 
            bot = self.bot

            inv = bot.inventory.daily

            # Єдине джерело істини — те саме, що використовує _run_claim_cycle.
            # can_claim_daily / can_claim_calendar означають інше (чи відомий стан
            # для виконання claim), а не "чи вже зроблено сьогодні" — використання
            # їх тут раніше давало all_done=False навіть коли все реально зібрано.
            needs_daily, needs_calendar = self._determine_needs(
                inv.last_daily_claimed, inv.last_calendar_claimed
            )
            all_done = not needs_daily and not needs_calendar

            delay_to_next_day, scheduled_time = self._delay_until_tomorrow(self.BASE_TIME, self._account_id, 180)
            target_time = self._target_time_today(scheduled_time)

            if all_done:
                return delay_to_next_day
            
            if is_today(target_time):
                return self._delay_when_time_passed(scheduled_time)
            
            # Якщо бонуси сьогодні ще не зібрані, але запланований час ще не настав:
            # вираховуємо затримку безпосередньо до сьогоднішнього запланованого часу.
            delay_until_today_target = (target_time - now()).total_seconds()
            return max(0.0, delay_until_today_target)     
        except ValueError as ex:
            log.error(ex)
            if str(ex) == "Account не доступний":
                self._last_attempt_failed = True
                return 300.0

    def _target_time_today(self, scheduled_time: str) -> datetime:
        # 1. Беремо поточний час із налаштованою часовою зоною вашого проекту
        n = now()
        # 2. Використовуємо нову безпечну функцію розбору з нашого модуля
        h, m = _parse_hh_mm(scheduled_time)
        
        # 3. Повертаємо об'єкт із заміненим часом
        return n.replace(hour=h, minute=m, second=0, microsecond=0)

    def _delay_until_tomorrow(self, base: str, account_id: str, jitter_minutes: int) -> tuple[float, str]:
        delay = seconds_until_tomorrow_time_stable(base, account_id, jitter_minutes)
        # Отримуємо стабільний час доби замість тривалості (duration)
        scheduled_time = self._get_scheduled_time_str(base, account_id, jitter_minutes)
        
        get_account_logger(self._account_id).info(
            f"[DailyMonitor] Обидва бонуси на сьогодні вже зібрано. "
            f"Наступний запуск заплановано на завтра о {scheduled_time} UTC (через {int(delay)}с)"
        )
        return max(0.0, delay), scheduled_time

    def _delay_when_time_passed(self, scheduled_time: str) -> float:
        log = get_account_logger(self._account_id)
        if self._last_attempt_failed:
            cooldown = 300.0
            log.warning(
                f"[DailyMonitor] Попередня спроба збору завершилась невдало. "
                f"Наступна спроба відкладена на {int(cooldown)}с (cooldown)"
            )
            return cooldown

        log.info(
            f"[DailyMonitor] Настав час збору бонусів ({scheduled_time} UTC) — запуск негайно"
        )
        return 0.0

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

            if needs_calendar:
                # _ensure_streak_known лише встановлює inv.can_claim_calendar / inv.day
                # (доступність та номер дня стріку) — last_calendar_claimed вона не
                # чіпає, тож "чи потрібен календарний бонус сьогодні" не змінюється
                # цим кроком. Раніше тут був хибний рекомпут через is_next_day(),
                # який використовував стару семантику ("рівно наступний день") і
                # застарілу локальну змінну last_calendar_claimed — прибрано.
                failed |= await self._ensure_streak_known(log, inv)

            if needs_daily:
                failed |= await self._claim_daily(log, inv, to_day)

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