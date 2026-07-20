"""
day_service/scheduler.py — DayScheduler.

Єдина відповідальність усього сервісу: для кожного зареєстрованого
account_id визначити момент, коли настав новий календарний день
(стабільний, індивідуальний для акаунта зсув — той самий hash-jitter
алгоритм, що раніше жив у DailyMonitor бізнес-застосунку), і публікувати
про це подію в Redis. Ніякого знання про daily-бонус / mining / quiz.

Ресурсна оптимізація: замість одного asyncio.Task на КОЖЕН акаунт (що
раніше було найпростішим рішенням, але для N акаунтів — N сплячих
корутин, кожна зі своїм стеком) тут ОДИН фоновий цикл на весь сервіс, що
тримає купу (heapq) "коли наступний акаунт має спрацювати" і спить рівно
до найближчої події. Додавання/видалення акаунта лише будить цей єдиний
цикл (asyncio.Event), а не створює/скасовує окрему задачу. Складність
для N акаунтів: O(log N) на реєстрацію/спрацювання замість O(N) сплячих
тасків одночасно.

Стійкість до перезапуску — як і раніше: спрацювання спершу пишеться в
SQLite (day_runs, PK (account_id, day)) і лише ПІСЛЯ успішного запису
публікується подія. Рестарт у межах того самого дня не дублює
оповіщення; пропущений під час простою день — доганяється один раз при
старті.
"""
from __future__ import annotations

import asyncio
import heapq
import logging
import sqlite3
import time
from dataclasses import dataclass

from day_service import db
from day_service.config import settings
from day_service.redis_bus import DayEventPublisher
from day_service.timeutil import day_token, get_tz, next_occurrence

log = logging.getLogger("day_service.scheduler")


@dataclass
class AccountSchedule:
    account_id: str
    base_time: str
    jitter_minutes: int
    scheduled_time: str = ""
    next_run_at: str = ""
    next_run_ts: float = 0.0
    last_day: str | None = None


class DayScheduler:
    def __init__(self, conn: sqlite3.Connection, publisher: DayEventPublisher) -> None:
        self._conn = conn
        self._publisher = publisher
        self._tz = get_tz(settings.timezone)

        self._accounts: dict[str, AccountSchedule] = {}
        # Heap-елемент: (next_run_ts, account_id). Ледаче видалення: при
        # спрацюванні перевіряємо, чи запис ще актуальний (account_id досі
        # зареєстрований і next_run_ts збігається з тим, що в _accounts) —
        # застарілі записи (після remove/force/re-add) просто пропускаються.
        self._heap: list[tuple[float, str]] = []
        self._wake = asyncio.Event()
        self._loop_task: asyncio.Task[None] | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        today = day_token(self._tz)
        for row in db.list_accounts(self._conn):
            self._register(row["account_id"], row["base_time"], row["jitter_minutes"], today)
        self._loop_task = asyncio.create_task(self._run(), name="day-scheduler-loop")
        log.info(f"[DayScheduler] стартувало, акаунтів: {len(self._accounts)}")

    async def stop(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except (asyncio.CancelledError, Exception):
                pass
            self._loop_task = None

    # ── Реєстрація акаунтів (викликається з command-consumer'а) ──────────────

    def add_account(self, account_id: str, base_time: str, jitter_minutes: int) -> AccountSchedule:
        db.upsert_account(self._conn, account_id, base_time, jitter_minutes)
        today = day_token(self._tz)
        sched = self._register(account_id, base_time, jitter_minutes, today)
        self._wake.set()
        return sched

    def remove_account(self, account_id: str) -> bool:
        existed = db.delete_account(self._conn, account_id)
        # Не чіпаємо heap — запис для цього account_id стане "застарілим"
        # (лениве видалення) і буде мовчки пропущений, коли до нього
        # дійде черга. Це дешевше за пошук/видалення довільного елемента
        # з heapq (O(N)).
        self._accounts.pop(account_id, None)
        return existed

    def status(self, account_id: str) -> AccountSchedule | None:
        return self._accounts.get(account_id)

    def all_status(self) -> list[AccountSchedule]:
        return list(self._accounts.values())

    async def force_trigger(self, account_id: str) -> bool | None:
        """Ручний позачерговий тригер. None, якщо акаунт не зареєстрований."""
        if account_id not in self._accounts:
            return None
        published = await self._fire_if_new(account_id)
        self._reschedule(account_id)
        self._wake.set()
        return published

    # ── Внутрішнє ─────────────────────────────────────────────────────────────

    def _register(self, account_id: str, base_time: str, jitter_minutes: int, today_token: str) -> AccountSchedule:
        last_day = db.last_triggered_day(self._conn, account_id)
        sched = AccountSchedule(
            account_id=account_id, base_time=base_time, jitter_minutes=jitter_minutes,
            last_day=last_day,
        )
        self._accounts[account_id] = sched
        self._push_next(sched, today_token, catch_up=True)
        return sched

    def _push_next(self, sched: AccountSchedule, today_token: str, catch_up: bool) -> None:
        scheduled_time, target, target_day = next_occurrence(
            sched.account_id, sched.base_time, sched.jitter_minutes, self._tz
        )
        sched.scheduled_time = scheduled_time
        sched.next_run_at = target.isoformat()
        sched.next_run_ts = target.timestamp()

        # Catch-up: якщо на сьогодні акаунт ще не спрацьовував, а
        # найближче МАЙБУТНЄ спрацювання (next_occurrence завжди повертає
        # майбутнє) — вже завтра, то сьогоднішній момент минув, поки
        # сервіс, ймовірно, не працював. Ставимо в heap "негайно" замість
        # завтра — компенсуємо пропущений день один раз.
        if catch_up and sched.last_day != today_token and target_day != today_token:
            sched.next_run_ts = time.time()

        heapq.heappush(self._heap, (sched.next_run_ts, sched.account_id))

    def _reschedule(self, account_id: str) -> None:
        sched = self._accounts.get(account_id)
        if sched is None:
            return
        self._push_next(sched, day_token(self._tz), catch_up=False)

    async def _run(self) -> None:
        try:
            while True:
                if not self._heap:
                    self._wake.clear()
                    await self._wake.wait()
                    continue

                next_ts, account_id = self._heap[0]
                delay = next_ts - time.time()

                if delay > 0:
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=delay)
                        # Розбудили достроково (реєстрація/force) — heap
                        # міг змінитись, перерахувати з нуля.
                        continue
                    except asyncio.TimeoutError:
                        pass

                heapq.heappop(self._heap)

                sched = self._accounts.get(account_id)
                if sched is None or sched.next_run_ts != next_ts:
                    # Застарілий запис (акаунт видалено, або вже
                    # перепланований через force_trigger) — пропускаємо.
                    continue

                await self._fire_if_new(account_id)
                self._push_next(sched, day_token(self._tz), catch_up=False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error(f"[DayScheduler] цикл впав: {exc}", exc_info=True)

    async def _fire_if_new(self, account_id: str) -> bool:
        today_token = day_token(self._tz)
        published = db.record_trigger(self._conn, account_id, today_token)
        sched = self._accounts.get(account_id)
        if sched is not None:
            sched.last_day = today_token
        if not published:
            log.debug(f"[{account_id}] день {today_token} вже було оголошено раніше — пропуск")
            return False

        await self._publisher.publish_new_day(account_id, today_token)
        return True
