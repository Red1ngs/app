"""
quiz/build.py — QuizProfession.

Відповідальність:
    ТІЛЬКИ виконання: отримати ask, зробити HTTP, оновити inventory, емітити події.

    КОЛИ запускати      — вирішує QuizMonitor (не Profession).
    СКИДАННЯ ЛІЧИЛЬНИКА — QuizMonitor в _on_daily_claimed (не Profession).
    ПЛАНУВАННЯ ЗАТРИМОК — QuizMonitor через asyncio.sleep(answer_delay).

Два режими (mode зберігається в inventory):
    daily — N правильних відповідей на день.
    fixed — N правильних відповідей один раз назавжди.

Intents:
    do_open     → відкрити quiz-сесію, зберегти питання в inventory
    do_answer   → відповісти на поточне питання
    get_status  → поточний стан
    set_config  → змінити mode / answer_limit / answer_delay
    force_restart → скинути стан (тільки inventory, QuizMonitor сам стартує)

Events що емітуються:
    quiz.session_opened   — {account_id, question_id}
    quiz.answered_correct — {account_id, question_id, correct_count, counter, limit}
    quiz.milestone        — {account_id, milestone, message, correct_count}
    quiz.limit_reached    — {account_id, counter, correct_count, mode}
    quiz.session_ended    — {account_id, reason, correct_count, mode}
"""
from __future__ import annotations

from logging import Logger
from typing import TYPE_CHECKING, Any, Optional

from src.core.runtime.profession import BaseProfession, RequestResult
from src.mangabuff.quiz.inventory import QuizInventory

if TYPE_CHECKING:
    from src.core.core_account import Account
    from src.core.runtime.request_router import RequestContext
    from src.core.runtime.scheduler import EventDrivenScheduler

from src.core.logging.loggers import get_account_logger


class QuizProfession(BaseProfession):
    """
    Profession «Квіз».

    Чистий виконавець: отримує ask → HTTP → inventory → emit.
    Не знає про таймінги, не планує наступний запуск, не скидає лічильник.
    """

    def __init__(self) -> None:
        self._account_id: str                              = ""
        self._scheduler:  Optional["EventDrivenScheduler"] = None
        self._bot:        Optional[Account]                = None

    @property
    def profession_id(self) -> str:
        return "quiz"

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def setup(self, scheduler: "EventDrivenScheduler", account_id: str) -> None:
        self._account_id = account_id
        self._scheduler  = scheduler

    async def restore_state(self, bot: "Account") -> None:
        inv: QuizInventory = bot.inventory.quiz
        inv.init_from_config(bot.app_config.quiz)
        
    async def teardown(self, scheduler: "EventDrivenScheduler", account_id: str) -> None:
        self._scheduler = None

    def check_guard(self, bot: "Account") -> bool:
        return not bool(bot.inventory.personal.is_banned)

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
            
            if ctx.account_id != self._account_id:
                raise ValueError(
                    f"account_id {ctx.account_id} не збігається з {self._account_id}"
                )

            shelduler = self._scheduler
            bot = self._scheduler.get_bot(self._account_id)
            if bot is None:
                raise ValueError(f"Бот для акаунта {self._account_id} не знайдений")

            if intent == "do_open":
                return await self._handle_do_open(shelduler, bot, log)
            if intent == "do_answer":
                return await self._handle_do_answer(shelduler, bot, log)
            if intent == "get_status":
                return await self._handle_get_status(bot)
            if intent == "set_config":
                return await self._handle_set_config(data, bot, log)
            if intent == "force_restart":
                return await self._handle_force_restart(shelduler, bot, log)
            if intent == "reset_daily":
                return await self._handle_reset_daily(shelduler, bot, log)
            return RequestResult.deny(f"unknown intent: {intent!r}")
    
        except ValueError as exc:
            log.warning(f"❌ {intent}: {exc}")
            return RequestResult.deny(str(exc))

    # ── do_open ───────────────────────────────────────────────────────────────

    async def _handle_do_open(
        self,
        scheduler: "EventDrivenScheduler",
        bot: "Account",
        log: "Logger"
    ) -> RequestResult:
        """Відкриває нову quiz-сесію. Зберігає перше питання в inventory."""
        inv: QuizInventory = bot.inventory.quiz

        if inv.session_active:
            log.debug("do_open: сесія вже активна, пропускаємо")
            return RequestResult.approve(data={"status": "already_open"})

        log.info("📝 Quiz: відкриваємо сесію…")
        cfg = bot.app_config.quiz
        question = await bot.safe_session.quiz_start(cfg)

        data = question.data
        if data is None:
            log.warning("⚠️ /quiz/start не відповів")
            raise ValueError("quiz_start returned None")

        inv.open_session(data)

        log.info(
            f"✅ Сесія відкрита | "
            f"q_id={data.get('id')} | "
            f"{data.get('question', '')[:60]}"
        )

        await scheduler.emit_event(
            "quiz.session_opened",
            {"account_id": self._account_id, "question_id": question.data.get("id")},
            source=self._account_id,
        )

        return RequestResult.approve(data={
            "question_id": data.get("id"),
            "question":    data.get("question", ""),
        })

    # ── do_answer ─────────────────────────────────────────────────────────────

    async def _handle_do_answer(
        self,
        scheduler: "EventDrivenScheduler",
        bot: "Account",
        log: "Logger"
    ) -> RequestResult:
        """
        Відповідає на поточне питання в inventory.

        Повертає data з полем status:
            "correct"   — правильно, сесія продовжується
            "milestone" — правильно + досягнення
            "limit"     — ліміт досягнуто, сесія закрита
            "restart"   — невірна відповідь, сесія закрита
            "no_next"   — сервер вичерпав питання, сесія закрита
        """    
        inv: QuizInventory = bot.inventory.quiz
        personal = bot.inventory.personal
            
        to_day = personal.to_day

        question = inv.current_question
        if question is None:
            log.warning("⚠️ do_answer: питання в inventory відсутнє")
            raise ValueError("no current question")

        answer_text: str = question.get("correct_text", question["answers"][0])

        log.info(
            f"❓ [Q#{question.get('id')}] "
            f"{question.get('question', '')[:60]} → «{answer_text}»"
        )

        cfg = bot.app_config.quiz
        result = await bot.safe_session.quiz_answer(answer_text, cfg)

        if result.data is None:
            log.warning("⚠️ /quiz/answer не відповів")
            raise ValueError("quiz_answer returned None")

        status = result.data.get("status")

        # ── Правильна відповідь ────────────────────────────────────────────────
        if status in ("success", "milestone"):
            inv.correct_count = result.data.get("correct_count", inv.correct_count + 1)
            counter = inv.increment_counter()

            log.info(
                f"✅ Вірно! "
                f"correct={inv.correct_count} | "
                f"counter={counter}/{inv.answer_limit} "
                f"(mode={inv.mode})"
            )

            await scheduler.emit_event(
                "quiz.answered_correct",
                {
                    "account_id":    self._account_id,
                    "question_id":   question.get("id"),
                    "correct_count": inv.correct_count,
                    "counter":       counter,
                        "limit":         inv.answer_limit,
                    },
                    source=self._account_id,
                )

            if status == "milestone":
                log.info(
                    f"🏅 Milestone! "
                    f"milestone={result.data.get('milestone')} "
                    f"message={result.data.get('message', '')!r}"
                )
                await scheduler.emit_event(
                    "quiz.milestone",
                    {
                        "account_id":    self._account_id,
                        "milestone":     result.data.get("milestone"),
                        "message":       result.data.get("message"),
                        "correct_count": inv.correct_count,
                    },
                    source=self._account_id,
                )

            # ── Ліміт досягнуто ───────────────────────────────────────────────
            if counter >= inv.answer_limit:
                log.info(
                    f"🎯 Ліміт досягнуто "
                    f"({counter}/{inv.answer_limit}, mode={inv.mode}) → закриваємо"
                )
                inv.answer_limit = counter
                inv.close_session(to_day)

                await scheduler.emit_event(
                    "quiz.limit_reached",
                    {
                        "account_id":    self._account_id,
                        "counter":       counter,
                        "correct_count": inv.correct_count,
                        "mode":          inv.mode,
                    },
                    source=self._account_id,
                )

                return RequestResult.approve(data={"status": "limit"})

            # ── Наступне питання ──────────────────────────────────────────────
            next_question = result.data.get("question")
            if next_question:
                inv.current_question = next_question
                log.info(f"➡️  Наступне питання id={next_question.get('id')}")
                return RequestResult.approve(data={
                    "status": "milestone" if status == "milestone" else "correct",
                })

            # Сервер вичерпав питання
            log.info("🏆 Сервер вичерпав питання")
            inv.close_session(to_day)
            return RequestResult.approve(data={"status": "no_next"})

        # ── Невірна відповідь / restart ───────────────────────────────────────
        if status == "restart":
            log.info(
                f"❌ Невірна відповідь. "
                f"Результат: {inv.correct_count}. "
                f"mode={inv.mode}. {result.data.get('message', '')}"
            )
            inv.close_session(to_day)

            await scheduler.emit_event(
                "quiz.session_ended",
                {
                    "account_id":    self._account_id,
                    "reason":        "restart",
                    "correct_count": inv.correct_count,
                    "mode":          inv.mode,
                },
                source=self._account_id,
            )

            return RequestResult.approve(data={"status": "restart"})

        log.warning(f"⚠️ Невідомий status: {status!r}")
        raise Exception(f"unknown quiz status: {status!r}")

    # ── get_status ────────────────────────────────────────────────────────────

    async def _handle_get_status(
        self,
        bot: "Account",
    ) -> RequestResult:
        inv: QuizInventory = bot.inventory.quiz
        q = inv.current_question
        return RequestResult.approve(data={
            "mode":             inv.mode,
            "answer_limit":     inv.answer_limit,
            "answer_delay":     inv.answer_delay,
            "session_active":   inv.session_active,
            "counter":          inv.current_counter(),
            "correct_count":    inv.correct_count,
            "last_quiz_date":   inv.last_quiz_date,
            "fixed_done":       inv.fixed_done,
            "current_question": q.get("question") if q else None,
        })

    # ── set_config ────────────────────────────────────────────────────────────

    async def _handle_set_config(
        self, 
        data: dict[str, Any], 
        bot: "Account",
        log: "Logger"
    ) -> RequestResult:
        inv: QuizInventory = bot.inventory.quiz 
        changed: dict[str, Any] = {}

        if "mode" in data:
            mode = data["mode"]
            if mode not in ("daily", "fixed"):
                raise ValueError("mode має бути 'daily' або 'fixed'")
            inv.mode = mode
            changed["mode"] = mode

        if "answer_limit" in data:
            limit = data["answer_limit"]
            if not isinstance(limit, int) or limit < 1:
                raise ValueError("answer_limit має бути цілим числом >= 1")
            inv.answer_limit = limit
            changed["answer_limit"] = limit

        if "answer_delay" in data:
            delay = data["answer_delay"]
            if not isinstance(delay, (int, float)) or delay < 0:
                raise ValueError("answer_delay має бути числом >= 0")
            inv.answer_delay = float(delay)
            changed["answer_delay"] = float(delay)

        if not changed:
            return RequestResult.deny(
                "нічого не змінено — передайте mode, answer_limit або answer_delay"
            )

        log.info(f"Quiz: конфіг оновлено → {changed}")
        return RequestResult.approve(data={"changed": changed})


    # ── reset_daily ───────────────────────────────────────────────────────────

    async def _handle_reset_daily(
        self,
        scheduler: "EventDrivenScheduler",
        bot: "Account",
        log: "Logger"
    ) -> RequestResult:
        """
        Скидає щоденний лічильник квіза після daily.claimed.

        Викликається QuizMonitor через ask() — монітор НЕ мутує inventory напряму.
        Збереження відбувається автоматично воркером після завершення задачі.
        """ 
        inv: QuizInventory = bot.inventory.quiz
        personal = bot.inventory.personal
        to_day = personal.to_day

        if inv.mode != "daily":
            return RequestResult.deny(f"reset_daily не застосовний для mode={inv.mode!r}")
            
        if inv.last_quiz_date == to_day and inv.current_counter() >= inv.answer_limit:
            log.info(
                "QuizProfession: reset_daily → ліміт вже вичерпано сьогодні, пропускаємо"
            )
            return RequestResult.deny("daily limit already reached today")

        inv.reset_daily_counter(to_day)
        inv.session_active   = False
        inv.current_question = None
        inv.last_quiz_date   = None  # знімаємо блок _should_run у QuizMonitor

        log.info(
            "QuizProfession: reset_daily → лічильник скинуто"
        )
        return RequestResult.approve(data={"status": "reset"})

    # ── force_restart ─────────────────────────────────────────────────────────

    async def _handle_force_restart(
        self,
        scheduler: "EventDrivenScheduler",
        bot: "Account",
        log: "Logger"
    ) -> RequestResult:
        """
        Скидає стан inventory. QuizMonitor сам підхопить через _should_run()
        і запустить новий цикл при наступній перевірці або на daily.claimed.
        """
        inv: QuizInventory = bot.inventory.quiz
        personal = bot.inventory.personal
        inv.session_active   = False
        inv.current_question = None

        if inv.mode == "daily":
            inv.reset_daily_counter(personal.to_day)
            inv.last_quiz_date = None
        else:
            inv.answers_done = 0
            inv.fixed_done   = False

        log.info("QuizProfession: force_restart")

        # Повідомляємо монітор щоб стартував негайно
        await scheduler.emit_event(
            "quiz.force_restarted",
            {"account_id": self._account_id},
            source=self._account_id,
        )

        return RequestResult.approve(data={"status": "restarting"})