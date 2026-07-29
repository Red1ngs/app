"""
startup_manager.py — послідовний запуск акаунтів при старті.

Порядок для кожного акаунта:
    1. connect_account()   — встановлює сесію
    2. setup_professions() — setup + attach monitors (потребує живої сесії)

Пауза між акаунтами запобігає паралельному флуду login-запитів.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.core.status import AccountStatus

if TYPE_CHECKING:
    from src.core.services.scheduler_service import SchedulerService

log = logging.getLogger("src.runtime.startup_manager")


@dataclass
class StartupConfig:
    connect_delay:   float = 5.0
    connect_timeout: float = 30.0
    skip_failed:     bool  = True
    connect_retries: int   = 3
    retry_backoff:   float = 5.0
    # Скільки акаунтів конектити ОДНОЧАСНО. Кожен акаунт ходить через
    # СВОЄ проксі (окремий _ProxyWorker в account-service), тобто
    # акаунти між собою мережево незалежні — попередня чисто послідовна
    # реалізація (delay=5s + до 3 ретраїв × timeout на КОЖЕН акаунт)
    # без потреби розтягувала старт великого парку акаунтів на
    # 5-10+ хвилин, протягом яких уже підключені акаунти просто чекали
    # своєї черги отримати монітори. concurrency=1 повертає стару чисто
    # послідовну поведінку, якщо раптом потрібно (напр. дуже обмежений
    # account-service чи спільний ресурс).
    concurrency:     int   = 4

    @classmethod
    def from_app_config(cls, cfg) -> "StartupConfig":
        s = getattr(cfg, "startup", None)
        if s is None:
            return cls()
        return cls(
            connect_delay=getattr(s, "connect_delay", 5.0),
            connect_timeout=getattr(s, "connect_timeout", 30.0),
            skip_failed=getattr(s, "skip_failed", True),
            connect_retries=getattr(s, "connect_retries", 3),
            retry_backoff=getattr(s, "retry_backoff", 5.0),
            concurrency=getattr(s, "concurrency", 4),
        )


class StartupManager:
    """
    Послідовний connect + setup профессій для кожного акаунта з паузою.

    Використання:
        sm = StartupManager(service, cfg)
        for aid in registered:
            sm.add(aid)
        await sm.run()
    """

    def __init__(self, service: "SchedulerService", cfg: StartupConfig | None = None) -> None:
        self._service = service
        self._cfg     = cfg or StartupConfig()
        self._queue:  list[str]             = []
        self._ok:     list[str]             = []
        self._failed: list[tuple[str, str]] = []

    def add(self, account_id: str) -> None:
        self._queue.append(account_id)

    @property
    def ok_accounts(self)     -> list[str]:             return list(self._ok)
    @property
    def failed_accounts(self) -> list[tuple[str, str]]: return list(self._failed)

    async def run(self) -> None:
        if not self._queue:
            log.info("[StartupManager] Черга порожня")
            return

        total = len(self._queue)
        concurrency = max(1, self._cfg.concurrency)
        log.info(
            f"[StartupManager] Паралельний запуск {total} акаунтів "
            f"(concurrency={concurrency}, стагер={self._cfg.connect_delay}s)"
        )

        # Раніше акаунти конектились СТРОГО послідовно (delay між кожним
        # + до 3 ретраїв на кожен), тобто час до "READY" зростав лінійно
        # з кількістю акаунтів (для 7 акаунтів — реально 5-6+ хвилин, і
        # весь цей час уже підключені акаунти просто чекали своєї черги
        # отримати профессії/монітори). Кожен акаунт ходить через СВОЄ
        # проксі (окремий _ProxyWorker на account-service), тому мережево
        # вони одне одному не заважають — можна конектити їх паралельно,
        # обмеживши лише кількість одночасних спроб (semaphore), щоб не
        # створювати сплеск паралельних login-запитів до account-service.
        #
        # Легкий стагер (idx * connect_delay / concurrency) лишається —
        # розводить МОМЕНТИ СТАРТУ навіть у межах однієї "хвилі" слотів,
        # замість того щоб усі concurrency акаунтів стартували в один й
        # той самий тік event loop'у.
        semaphore = asyncio.Semaphore(concurrency)
        order_lock = asyncio.Lock()
        completed = 0

        async def _worker(idx: int, account_id: str) -> None:
            nonlocal completed
            stagger = self._cfg.connect_delay * idx / concurrency
            if stagger > 0:
                await asyncio.sleep(stagger)

            async with semaphore:
                async with order_lock:
                    completed += 1
                    pos = completed
                log.info(f"[StartupManager] [{pos}/{total}] '{account_id}' …")
                try:
                    await self._start_one(account_id)
                except Exception as exc:
                    self._failed.append((account_id, str(exc)))
                    log.error(f"[StartupManager] ✗ '{account_id}': {exc}")
                    if not self._cfg.skip_failed:
                        raise

        tasks = [
            asyncio.create_task(_worker(idx, account_id))
            for idx, account_id in enumerate(self._queue)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # skip_failed=False: перше "справжнє" виключення (не з наших уже
        # захоплених у _failed) прокидаємо назовні, як і стара поведінка
        # з raise усередині циклу.
        if not self._cfg.skip_failed:
            for r in results:
                if isinstance(r, Exception):
                    raise r

        log.info(
            f"[StartupManager] READY — підключено: {len(self._ok)}/{total}"
            + (f", невдало: {[a for a, _ in self._failed]}" if self._failed else "")
        )

    async def _start_one(self, account_id: str) -> None:
        scheduler = self._service._scheduler

        ok = await self._connect_with_retries(account_id)
        if not ok:
            bot = scheduler.get_bot(account_id)
            err = (bot.error if bot else None) or "connect() повернув False"
            self._failed.append((account_id, err))
            log.error(f"[StartupManager] ✗ '{account_id}': {err}")
            if not self._cfg.skip_failed:
                raise RuntimeError(err)
            return

        professions = self._service._build_professions(account_id)
        await scheduler.setup_professions(account_id, professions)
        self._ok.append(account_id)
        log.info(f"[StartupManager] ✓ '{account_id}' готово")

    async def _connect_with_retries(self, account_id: str) -> bool:
        """
        Повторює connect_account() до `connect_retries` разів з паузою
        `retry_backoff` між спробами.

        Раніше акаунт мав рівно ОДНУ спробу підключення на весь час життя
        процесу: транзитна мережева гикавка (наприклад, 5-секундний
        timeout проксі рівно в момент старту контейнера) назавжди ховала
        акаунт у стан ERROR — без ретраю тут єдиним "ліками" лишався
        ручний рестарт усього compose-стеку.

        Забанені акаунти (bot.status == DEAD, виставляється в
        Account.connect() при remote.status == "banned") — не ретраїмо:
        подальші спроби нічого не змінять, тільки марно палять час і
        проксі-запити.
        """
        scheduler = self._service._scheduler

        for attempt in range(1, self._cfg.connect_retries + 1):
            if await scheduler.connect_account(account_id):
                return True

            bot = scheduler.get_bot(account_id)
            if bot is not None and bot.status is AccountStatus.DEAD:
                return False

            if attempt < self._cfg.connect_retries:
                log.warning(
                    f"[StartupManager] [{account_id}] спроба {attempt}/{self._cfg.connect_retries} "
                    f"невдала ({(bot.error if bot else None) or '?'}), "
                    f"повтор через {self._cfg.retry_backoff}s …"
                )
                await asyncio.sleep(self._cfg.retry_backoff)

        return False