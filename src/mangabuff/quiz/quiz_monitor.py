"""
quiz/quiz_monitor.py — QuizMonitor.

Відповідальність:
    КОЛИ і ЯК запускати цикл квіза — тут.
    ЩО САМЕ робити (HTTP, inventory) — в QuizProfession через ask().

Логіка режимів:

    daily — N правильних відповідей на день.
        • Стартує після daily.claimed (скидає лічильник).
        • Якщо бот перезапустився — відновлює цикл якщо ліміт ще не вичерпано.
        • Зупиняється коли quiz.limit_reached АБО quiz.session_ended
          і last_quiz_date == to_day.

    fixed — N правильних відповідей один раз назавжди.
        • Стартує одразу при attach().
        • Зупиняється коли quiz.limit_reached → більше не планує.
        • fixed_done=True → detach не потрібен, монітор просто мовчить.

Цикл open → answer:
    1. ask("do_open")   — відкрити сесію, зберегти питання в inventory
    2. ask("do_answer") — відповісти, зберегти наступне питання або закрити сесію
    3. Повторювати п.2 з затримкою answer_delay поки сесія активна і ліміт не досягнуто

Таймінг:
    - Після do_open чекаємо answer_delay перед першою відповіддю.
    - answer_delay витримується ЗАВЖДИ — і після успіху, і після 429.
    - Після break (rejected) цикл завершується вже після sleep,
      тому новий цикл від daily.claimed/restart не прийде на "гарячий" сервер.

Події що слухає:
    daily.claimed          → скинути лічильник + стартувати (тільки daily)
    quiz.limit_reached     → зупинити цикл
    quiz.session_ended     → зупинити цикл (restart від сервера)

Події що emitуються в QuizProfession (не тут):
    quiz.session_opened, quiz.answered_correct, quiz.milestone,
    quiz.limit_reached, quiz.session_ended
"""
from __future__ import annotations

import asyncio
from logging import Logger
from typing import TYPE_CHECKING, Any, Optional

from src.core.monitoring.looping_monitor import LoopingMonitor
from src.core.logging.loggers import get_account_logger
from src.utils.time import is_next_day

if TYPE_CHECKING:
    from src.core.runtime.scheduler import EventDrivenScheduler
    from src.core.core_account import Account


class QuizMonitor(LoopingMonitor):
    """
    Монітор квіза. Веде цикл open/answer для одного акаунта.

    Не містить IO — тільки планування ask().
    Стан (mode, answer_limit, answer_delay, session_active, fixed_done)
    читається з QuizInventory при кожному циклі.
    """

    @property
    def monitor_id(self) -> str:
        return "quiz"

    def __init__(self) -> None:
        super().__init__()
        self._account_id: str                              = ""
        self._scheduler:  Optional["EventDrivenScheduler"] = None
        self._bot:        Optional["Account"]              = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def attach(
        self,
        scheduler:  "EventDrivenScheduler",
        account_id: str,
    ) -> None:
        self._account_id = account_id
        self._scheduler  = scheduler
        self._bot = scheduler.get_bot(account_id)

        scheduler.subscribe("daily.claimed",        self._on_daily_claimed)
        scheduler.subscribe("quiz.limit_reached",   self._on_limit_reached)
        scheduler.subscribe("quiz.session_ended",   self._on_session_ended)
        
        self._prepare_cycle()

        # Відновлення після перезапуску
        if self._bot:
            delay = self._bot.inventory.quiz.answer_delay
        else:
            delay = 1.0
        await self._schedule_next(delay=delay)

    async def detach(
        self,
        scheduler:  "EventDrivenScheduler",
        account_id: str,
    ) -> None:
        self._stop_loop()
        self._scheduler = None

    # ── Cycle scheduling ──────────────────────────────────────────────────────
    #
    # Власне планування (delay/скасування) винесено у LoopingMonitor.
    # _run_cycle() нижче лишається бізнес-логікою: один прохід open → answer × N.

    def _loop_logger(self) -> Logger:
        return get_account_logger(self._account_id)
    
    async def _interval(self) -> float:
        """
        QuizMonitor завжди явно передає delay у _schedule_next()
        (answer_delay при attach/reset_daily), тому окремого
        обчислення інтервалу не потрібно. Реалізовано лише щоб
        _schedule_next() без аргументу не впав на NotImplementedError,
        а безпечно не планував цикл.
        """
        return self._get_answer_delay()

    def _prepare_cycle(self) -> None:
        """
        Перевіряє чи треба скинути лічильник (daily) і логування стану монітора після перезапуску.
        Викликається при attach()
        """
        bot = self._bot
        
        if self._scheduler is None:
            raise ValueError("Scheduler не доступний")
        
        if bot is None:
            raise ValueError("Scheduler не доступний")
        
        inv = bot.inventory.quiz 
        personal = bot.inventory.personal
        to_day = personal.to_day
        
        if inv.mode == "daily" and is_next_day(inv.answers_reset_date, to_day):
            inv.reset_daily_counter(to_day)
            inv.session_active   = False
            inv.current_question = None
            get_account_logger(self._account_id).info(
                "Quiz(daily): новий день → лічильник скинуто"
            )

        get_account_logger(self._account_id).info(
            f"QuizProfession відновлено: "
            f"mode={inv.mode!r} "
            f"counter={inv.current_counter()}/{inv.answer_limit} "
            f"delay={inv.answer_delay}s "
            f"session_active={inv.session_active} "
            f"fixed_done={inv.fixed_done}"
        )

    # ── Cycle logic ───────────────────────────────────────────────────────────

    async def _run_cycle(self) -> None:
        """
        Один повний цикл: open → answer × N.

        Таймінг:
            answer_delay береться з inventory на початку кожної ітерації.
            sleep завжди виконується — і після успіху, і після відмови (429).
            Після do_open також чекаємо answer_delay перед першою відповіддю,
            щоб сервер встиг "охолонути" після попередньої сесії.
        """
        scheduler = self._scheduler
        if scheduler is None or not self._loop_enabled:
            return

        log = self._loop_logger()
        
        if not self._should_run():
            log.debug("[QuizMonitor] _run_cycle: умова запуску не виконана → пропускаємо")
            return

        # ── Відкриваємо сесію якщо не активна ────────────────────────────────
        inv = self._bot.inventory.quiz if self._bot else None
        if inv is not None and not inv.session_active:
            log.info("[QuizMonitor] → ask quiz do_open")
            result = await scheduler.ask(
                account_id    = self._account_id,
                profession_id = "quiz",
                intent        = "do_open",
                caller        = "quiz_monitor",
            )
            if not result.approved:
                log.warning(f"[QuizMonitor] do_open відхилено: {result.reason}")
                return

            # Затримка після відкриття сесії — перед першою відповіддю.
            # Захист від 429 якщо попередня сесія закінчилась щойно.
            answer_delay = self._get_answer_delay()
            if answer_delay > 0:
                await asyncio.sleep(answer_delay)

        # ── Цикл відповідей ────────────────────────────────────────────────────
        while self._loop_enabled and self._should_run():
            inv = self._bot.inventory.quiz if self._bot else None
            if inv is None or not inv.session_active:
                break

            answer_delay = inv.answer_delay

            log.info("[QuizMonitor] → ask quiz do_answer")
            result = await scheduler.ask(
                account_id    = self._account_id,
                profession_id = "quiz",
                intent        = "do_answer",
                caller        = "quiz_monitor",
            )

            # Завжди чекаємо answer_delay — незалежно від результату.
            # Якщо відповідь відхилена (429), пауза критична перед break,
            # щоб новий цикл (від daily.claimed чи restart) не прийшов
            # на "гарячий" сервер.
            if answer_delay > 0:
                await asyncio.sleep(answer_delay)

            if not result.approved:
                log.warning(f"[QuizMonitor] do_answer відхилено: {result.reason}")
                break

            # Profession емітує quiz.limit_reached / quiz.session_ended →
            # _on_limit_reached / _on_session_ended зупинять цикл.

    # ── State helpers ─────────────────────────────────────────────────────────

    def _waiting_for_daily(self) -> bool:
        """
        True = треба чекати daily.claimed перед стартом квіза.
        False = можна починати (daily profession відсутня або бонус вже зібрано).

        Логіка ідентична ReadingMonitor._waiting_for_daily():
          - Якщо profession "daily" не зареєстрована → False.
          - Якщо daily зібрано сьогодні (last_daily_claimed == to_day) → False.
          - Інакше → True.
        """
        bot = self._bot

        if self._scheduler is None:
            raise ValueError("Scheduler не доступний")
        
        if bot is None:
            raise ValueError("Account не доступний")
        
        if not self._scheduler.has_profession(self._account_id, "daily"):
            return False
        
        personal = bot.inventory.personal
        daily = bot.inventory.daily
        assert daily.last_daily_claimed is not None
        
        result = is_next_day(daily.last_daily_claimed, personal.to_day, strictly_tomorrow=False)

        return result

    def _should_run(self) -> bool:
        """
        Чи можна зараз запускати/продовжувати цикл.

        daily: ліміт не вичерпано сьогодні і daily вже зібрано
        fixed: fixed_done == False
        """
        log = self._loop_logger()
        bot = self._bot

        if self._scheduler is None:
            raise ValueError("Scheduler не доступний")
        
        if bot is None:
            raise ValueError("Account не доступний")
        
        inv = bot.inventory.quiz

        if inv.mode == "fixed":
            return not inv.fixed_done
        
        if inv.answer_limit == inv.answers_today:
            log.info("[QuizMonitor] ліміт відповідей досягнуто → цикл не стартує")
            return False

        # daily — чекаємо поки бонус не зібрано
        if self._waiting_for_daily():
            log.info(
                "[QuizMonitor] daily ще не зібрано — "
                "чекаємо daily.claimed (не плануємо наступний цикл)"
            )
            return False
        
        return inv.current_counter() < inv.answer_limit

    def _get_answer_delay(self) -> float:
        inv = self._bot.inventory.quiz if self._bot else None
        return float(inv.answer_delay) if inv is not None else 5.0

    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_daily_claimed(self, payload: dict[str, Any]) -> None:
        """Daily зібрано → просимо profession скинути лічильник, стартуємо цикл."""
        if payload.get("account_id") != self._account_id:
            return

        inv = self._bot.inventory.quiz if self._bot else None
        if inv is None or inv.mode != "daily":
            return

        scheduler = self._scheduler
        if scheduler is None:
            return

        log = self._loop_logger()
        log.info("[QuizMonitor] daily.claimed → ask quiz reset_daily")

        result = await scheduler.ask(
            account_id    = self._account_id,
            profession_id = "quiz",
            intent        = "reset_daily",
            caller        = "quiz_monitor",
        )

        if not result.approved:
            log.info(f"[QuizMonitor] reset_daily відхилено: {result.reason}")
            return

        # Якщо старий цикл ще не завершився (спить після 429) —
        # _schedule_next скасує його через _cancel_wakeup(), але новий
        # стартує з answer_delay затримкою, щоб не бити в "гарячий" сервер.
        delay = self._get_answer_delay()
        log.info(f"[QuizMonitor] reset_daily approved → стартуємо цикл (delay={delay}s)")
        await self._schedule_next(delay=delay)

    async def _on_limit_reached(self, payload: dict[str, Any]) -> None:
        """Ліміт досягнуто → зупиняємо поточний цикл."""
        if payload.get("account_id") != self._account_id:
            return

        inv = self._bot.inventory.quiz if self._bot else None
        mode = inv.mode if inv is not None else "?"

        get_account_logger(self._account_id).info(
            f"[QuizMonitor] quiz.limit_reached → зупиняємо цикл (mode={mode})"
        )

        if mode == "fixed":
            # fixed_done=True вже виставлено в profession — монітор більше
            # не має планувати нові цикли, навіть якщо щось спробує його
            # розбудити (напр. запізнілий daily.claimed).
            self._stop_loop()
        else:
            self._cancel_wakeup()

    async def _on_session_ended(self, payload: dict[str, Any]) -> None:
        """Сервер скинув сесію (restart) → зупиняємо поточний цикл."""
        if payload.get("account_id") != self._account_id:
            return

        get_account_logger(self._account_id).info(
            "[QuizMonitor] quiz.session_ended → зупиняємо цикл до наступного daily.claimed"
        )
        self._cancel_wakeup()