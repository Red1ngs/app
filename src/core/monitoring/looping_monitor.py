"""
src/core/monitoring/looping_monitor.py — LoopingMonitor.

Навіщо потрібен:
    BaseMonitor лишає реалізацію "коли прокинутись" повністю на підклас.
    У результаті DailyMonitor, MiningMonitor, ReadingMonitor і QuizMonitor
    незалежно один від одного відтворювали той самий шаблон:

        - зберігання власного asyncio.Task ("будильника")
        - скасування попереднього будильника перед плануванням нового
        - asyncio.sleep(delay) + виклик одного методу з бізнес-логікою
        - try/except CancelledError / Exception навколо фонового виконання

    LoopingMonitor виносить цей шаблон в один клас, лишаючи підкласу
    тільки бізнес-логіку одного циклу.

Відмінність від BaseMonitor:
    BaseMonitor   — контракт монітора взагалі (attach/detach, monitor_id).
    LoopingMonitor — те саме + готовий "двигун" внутрішнього циклу
                     для моніторів, яким має власний таймінг
                     (а не лише реагують на події EventBus).

    Монітори, які повністю event-driven і не мають власного циклу
    пробудження, успадковують BaseMonitor напряму — їм LoopingMonitor
    не потрібен.

Контракт підкласу:
    async def _run_cycle(self) -> None
        Обов'язково. Одна ітерація циклу: власні ask()-и, рішення,
        гілкування. Викликається після asyncio.sleep(delay).

        LoopingMonitor НЕ викликає _schedule_next() автоматично після
        _run_cycle() — рішення "чи планувати далі і через скільки"
        лишається за підкласом, бо в частини моніторів (reading, quiz)
        воно залежить від результату ask() та emit-нутих подій, а не
        є автоматичним.

    def _interval(self) -> float
        Опційно. Викликається з _schedule_next(delay=None), тобто коли
        підклас не задає затримку явно. Від'ємне значення означає
        "не планувати" (монітор засинає до зовнішньої події, напр.
        reader.chapters_exhausted). Підклас, що завжди передає delay
        явно, може лишити реалізацію за замовчуванням (вона підійме
        NotImplementedError, якщо її все ж викличуть).

    def _loop_logger(self) -> Logger
        Обов'язково. Логер, яким LoopingMonitor звітує про необроблені
        виключення всередині циклу.

Приклад:
    class ReadingMonitor(LoopingMonitor):
        monitor_id = "reading"

        async def attach(self, scheduler, account_id):
            self._scheduler = scheduler
            scheduler.subscribe("loader.chapters_ready", self._on_chapters_ready)
            self._schedule_next(delay=0.0)

        async def detach(self, scheduler, account_id):
            self._stop_loop()
            self._scheduler = None

        async def _run_cycle(self) -> None:
            await self._send_ask()

        def _interval(self) -> float:
            ...

        def _loop_logger(self) -> Logger:
            return self.log
"""
from __future__ import annotations

from abc import abstractmethod
import asyncio
from logging import Logger
from typing import TYPE_CHECKING, Optional

from src.core.logging.loggers import get_account_logger
from src.core.monitoring.monitor import BaseMonitor

if TYPE_CHECKING:
    from src.core.core_account import Account
    from src.core.runtime.scheduler import EventDrivenScheduler


# ─────────────────────────────────────────────────────────────────────────────
# LoopingMonitor
# ─────────────────────────────────────────────────────────────────────────────

class LoopingMonitor(BaseMonitor):
    """
    Базовий клас для моніторів із власним внутрішнім циклом пробудження.

    Підкласи НЕ повинні самі створювати asyncio.Task для пробудження —
    для цього є _schedule_next() / _cancel_wakeup() / _stop_loop().
    Підклас відповідає лише за _run_cycle() (що робити) та, опційно,
    за _interval() (коли прокинутись наступного разу).
    """

    def __init__(self) -> None:
        self._account_id = ""
        self._wakeup_task:  Optional["asyncio.Task[None]"]   = None
        self._loop_enabled: bool                             = True
        self._scheduler:    Optional["EventDrivenScheduler"] = None
        self._bot:          Optional["Account"]              = None
        self._log:          Optional["Logger"]               = None

    # ── Хуки для підкласу ────────────────────────────────────────────────────

    @abstractmethod
    async def _run_cycle(self) -> None:
        """Одна ітерація циклу. Підклас зобов'язаний перевизначити."""
        raise NotImplementedError(
            f"{type(self).__name__} повинен перевизначити _run_cycle()"
        )

    @abstractmethod
    async def _interval(self) -> float:
        """
        Затримка (сек) до наступного пробудження, коли delay не переданий
        явно у _schedule_next(). Від'ємне значення — "не планувати".

        Async — бо частині моніторів (напр. MiningMonitor) потрібно
        await всередині обчислення (перевірка стану через scheduler.ask()
        чи emit_event()). Підклас без такої потреби просто оголошує
        `async def _interval(self) -> float:` зі звичайним синхронним
        тілом — зайвого await не додає й нічого не коштує.

        Підклас, що завжди передає delay явно у _schedule_next(), може
        лишити цю реалізацію як є.
        """
        raise NotImplementedError(
            f"{type(self).__name__} повинен перевизначити _interval() "
            f"або завжди викликати _schedule_next(delay=...) явно"
        )

    # ── Guard-властивості ────────────────────────────────────────────────────
    #
    # Майже кожен метод монітора починається з "якщо scheduler/bot відсутній —
    # кинути ValueError". Замість того щоб писати ці два if у кожному методі,
    # підклас зберігає scheduler/bot у self._scheduler / self._bot (як і раніше)
    # і звертається до них через ці властивості — перевірка на None відбувається
    # один раз, всередині властивості.
    #
    #   до:                                      після:
    #     if self._scheduler is None:              scheduler = self.scheduler
    #         raise ValueError("...")               bot       = self.bot
    #     if bot is None:
    #         raise ValueError("...")
    #
    # Підклас, що не зберігає bot окремим полем (напр. лишень бере його з
    # scheduler.get_bot() на льоту), може не використовувати self.bot.

    @property
    def account_id(self) -> str:
        return self._account_id
    
    @account_id.setter
    def account_id(self, account_id: str) -> None:
        self._account_id = account_id
        
    @property
    def scheduler(self) -> "EventDrivenScheduler":
        if self._scheduler is None:
            raise ValueError("Scheduler не доступний")
        return self._scheduler
    
    @scheduler.setter
    def scheduler(self, scheduler: "EventDrivenScheduler") -> None:
        self._scheduler = scheduler

    @property
    def bot(self) -> "Account":
        if self._bot is None:
            bot  = self.scheduler.get_bot(self._account_id)
            if bot is None:
                raise ValueError("Account не доступний")
            self._bot = bot
        return self._bot
    
    @property
    def log(self) -> "Logger":
        if self._log is None:
            self._log = get_account_logger(self.account_id)
        return self._log

    # ── Планування циклу ─────────────────────────────────────────────────────

    async def _schedule_next(self, delay: Optional[float] = None) -> None:
        """
        Скасовує поточне очікування (якщо є) і планує наступний виклик
        _run_cycle() через delay секунд.

            delay is None → береться з _interval()
            delay < 0.0   → цикл НЕ планується (сон до зовнішньої події)

        Якщо цикл зупинено через _stop_loop(), виклик є no-op — щоб
        детач/термінальні стани (напр. QuizMonitor у режимі fixed після
        limit_reached) не могли бути випадково "розбуджені" запізнілим
        колбеком.

        Метод async лише заради єдиного інтерфейсу з рештою lifecycle-хуків
        (attach/detach також async) — сам він нічого не очікує, а лише
        реєструє фонову задачу через asyncio.ensure_future().
        """
        self._cancel_wakeup()

        if not self._loop_enabled:
            return

        if delay is None:
            delay = await self._interval()

        if delay < 0.0:
            return

        self._wakeup_task = asyncio.ensure_future(self._sleep_and_run(delay))

    async def _sleep_and_run(self, delay: float) -> None:
        """asyncio.sleep(delay) + _run_cycle() з єдиною точкою обробки помилок."""
        try:
            await asyncio.sleep(delay)
            await self._run_cycle()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.log.error(
                f"[{type(self).__name__}] Помилка у фоновому циклі: {exc}",
                exc_info=True,
            )

    def _cancel_wakeup(self) -> None:
        """Скасовує заплановане пробудження, якщо воно є."""
        if self._wakeup_task and not self._wakeup_task.done():
            self._wakeup_task.cancel()
        self._wakeup_task = None

    def _stop_loop(self) -> None:
        """
        Остаточно вимикає цикл: скасовує пробудження і забороняє подальше
        планування через _schedule_next(), поки хтось явно не викличе
        _resume_loop().

        Використовується у detach() і для "незворотних" станів (напр.
        QuizMonitor у режимі fixed після quiz.limit_reached).
        """
        self._loop_enabled = False
        self._cancel_wakeup()

    def _resume_loop(self) -> None:
        """Знову дозволяє планування циклу після _stop_loop()."""
        self._loop_enabled = True

    @property
    def loop_running(self) -> bool:
        """True, якщо зараз є активна задача очікування пробудження."""
        return self._wakeup_task is not None and not self._wakeup_task.done()

    def __repr__(self) -> str:
        return (
            f"<{type(self).__name__} id={self.monitor_id!r} "
            f"loop_running={self.loop_running}>"
        )