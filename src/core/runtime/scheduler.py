"""
scheduler.py — EventDrivenScheduler. Єдиний Scheduler в системі.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Optional, TYPE_CHECKING

from src.core.monitoring.account_monitors import AccountMonitors

if TYPE_CHECKING:
    from src.core.runtime.profession import BaseProfession
    from src.core.inventory.model import DynamicInventories

from src.core.core_account import Account
from src.core.logging.loggers import get_scheduler_logger
from src.core.runtime.event_bus import EventBus, EventCallback
from src.core.runtime.request_router import RequestContext, RequestRouter
from src.core.runtime.conditions import Condition
from src.core.runtime.profession import BaseProfession, RequestResult
from src.core.runtime.profession_spec import profession_registry
from src.core.runtime.proxy_queue import proxy_queue_manager
from src.core.status import AccountStatus

log = get_scheduler_logger()


def _split_evenly(items: list[Any], n: int) -> list[list[Any]]:
    if n <= 0:
        return []
    size = max(1, -(-len(items) // n))
    return [items[i: i + size] for i in range(0, len(items), size)]


class AccountContainer:
    """
    Self-contained менеджер стану та здібностей одного акаунта.
    Керує життєвим циклом професій та їхніх моніторів як єдиним цілим.
    """
    def __init__(
        self, 
        bot: "Account", 
        guard: Optional["Condition"] = None
    ):
        self.bot = bot
        self.guard = guard
        self.monitors = AccountMonitors(bot.account_id)
        self.professions: dict[str, "BaseProfession"] = {}

    def check_account_guard(self, inv: "DynamicInventories") -> bool:
        return self.guard is None or self.guard(inv)

    def profession_list(self) -> list["BaseProfession"]:
        return list(self.professions.values())

    def has_profession(self, profession_id: str) -> bool:
        return profession_id in self.professions

    def get_profession(self, profession_id: str) -> Optional["BaseProfession"]:
        return self.professions.get(profession_id)

    # ── Розумне керування життєвим циклом ────────────────────────────────────

    async def attach_profession(
        self,
        scheduler: "EventDrivenScheduler",
        profession: "BaseProfession",
        router: "RequestRouter"
    ) -> bool:
        
        """
        Комплексно додає професію: setup -> restore_state -> attach_monitors.
        """
        account_id = self.bot.account_id
        pid = profession.profession_id

        if pid in self.professions:
            log.warning(f"[{account_id}] profession {pid!r} вже зареєстрована")
            return True

        try:
            # 1. Ініціалізація професії
            await profession.setup(scheduler, account_id)
            router.register(account_id, profession)
            await profession.restore_state(self.bot)
            
            # 2. Зберігаємо у стан
            self.professions[pid] = profession

            # 3. Автоматично підтягуємо монітори зі специфікації
            if self.bot.is_connected:
                monitor_ids = profession_registry.monitors_for(pid)
                await self.monitors.attach_all(scheduler, monitor_ids)

            log.info(f"[{account_id}] {pid!r} ready (monitors attached)")
            return True

        except Exception as e:
            log.error(f"[{account_id}] setup {pid!r} failed: {e}", exc_info=True)
            # Rollback у разі помилки
            router.unregister(account_id, pid)
            self.professions.pop(pid, None)
            return False

    async def detach_profession(
        self,
        scheduler: "EventDrivenScheduler", 
        profession_id: str, 
        event_bus: "EventBus", 
        router: "RequestRouter"
    ) -> None:
        
        """
        Комплексно видаляє професію: teardown -> router unregister -> detach_unused_monitors.
        """
        account_id = self.bot.account_id
        profession = self.professions.pop(profession_id, None)
        
        if not profession:
            return

        # 1. Зупиняємо саму професію
        try:
            event_bus.unsubscribe_owner(profession)
            await profession.teardown(scheduler, account_id)
        except Exception as e:
            log.error(f"[{account_id}] teardown {profession_id!r} error: {e}")

        router.unregister(account_id, profession_id)

        # 2. Розумно зачищаємо монітори (тільки ті, що більше нікому не потрібні)
        await self.sync_monitors(scheduler, event_bus)
        log.info(f"[{account_id}] profession {profession_id!r} removed")

    async def sync_monitors(self, scheduler: "EventDrivenScheduler", bus: "EventBus") -> None:
        """
        Синхронізує активні монітори з поточним набором професій.
        Видаляє зайві, підключає відсутні (якщо потрібно).
        """
        if not self.bot.is_connected or self.bot.status == AccountStatus.SUSPENDED:
            return

        active_prof_ids = list(self.professions.keys())
        needed_monitors = set(profession_registry.all_monitors_for(active_prof_ids))
        current_monitors = set(self.monitors.active_ids())

        # Що треба видалити:
        to_remove = current_monitors - needed_monitors
        if to_remove:
            await self.monitors.detach_many(scheduler, bus, list(to_remove))

        # Що треба додати (на випадок resume або lazy connect):
        to_add = needed_monitors - current_monitors
        if to_add:
            await self.monitors.attach_all(scheduler, list(to_add))

    async def teardown_all(
        self, 
        scheduler: "EventDrivenScheduler",
        bus: "EventBus", 
        route: "RequestRouter"
    ) -> None:
        """Повне вимкнення всього контейнера (при видаленні або паузі)."""
        await self.monitors.detach_all(scheduler, bus)
        # Створюємо копію ключів, бо detach_profession модифікує словник
        for pid in list(self.professions.keys()):
            await self.detach_profession(scheduler, pid, bus, route)

# ─────────────────────────────────────────────────────────────────────────────
# EventDrivenScheduler
# ─────────────────────────────────────────────────────────────────────────────

class EventDrivenScheduler:
    """Singleton runtime kernel."""

    _instance:  Optional["EventDrivenScheduler"] = None
    _init_lock: threading.Lock = threading.Lock()

    # ── Singleton ─────────────────────────────────────────────────────────────

    @classmethod
    async def initialize(
        cls,
        on_dead: Optional[Callable[[Account], None]] = None,
    ) -> "EventDrivenScheduler":
        with cls._init_lock:
            if cls._instance is not None:
                raise RuntimeError("EventDrivenScheduler вже ініціалізований.")
            inst = cls.__new__(cls)
            inst._init(on_dead)
            # initialize() — async, отже зараз гарантовано є running loop.
            # Саме цей loop стає "домашнім" для всіх Account/BotSession,
            # створених через цей scheduler.
            inst._home_loop = asyncio.get_running_loop()
            cls._instance = inst
            return inst

    @classmethod
    def get_instance(cls) -> "EventDrivenScheduler":
        if cls._instance is None:
            raise RuntimeError("EventDrivenScheduler не ініціалізований.")
        return cls._instance

    @classmethod
    async def _reset_for_tests(cls) -> None:
        with cls._init_lock:
            if cls._instance is not None:
                try:
                    await cls._instance.stop()
                except Exception:
                    pass
            cls._instance = None

    # ── Init ──────────────────────────────────────────────────────────────────

    def _init(self, on_dead: Optional[Callable[[Account], None]]) -> None:
        self._on_dead = on_dead

        self._containers:  dict[str, AccountContainer] = {}
        self._lock        = threading.Lock()

        self._event_bus    = EventBus()
        self._router       = RequestRouter()
        self._async_loop:  Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._loading_lock: Optional[asyncio.Lock] = None

        # "Домашній" loop — той, у якому виконався initialize().
        # Усі Account/BotSession (а отже curl_cffi.AsyncSession) живуть і
        # використовуються тільки тут. Будь-який код, що звертається до
        # scheduler'а з іншого потоку/loop'у (admin-bot, тести), повинен
        # переноситись сюди через SchedulerService._run_on_home_loop(),
        # інакше curl_cffi впаде з "Future attached to a different loop".
        self._home_loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def home_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        return self._home_loop

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._async_loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._run_async_loop,
            daemon=True,
            name="scheduler-async",
        )
        self._loop_thread.start()

        self._wait_for_loop_ready()
        log.info("EventDrivenScheduler started")

    def _wait_for_loop_ready(self, timeout: float = 5.0) -> None:
        """Чекає поки asyncio loop в окремому потоці реально запустився."""
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._async_loop and self._async_loop.is_running():
                return
            time.sleep(0.01)
        raise RuntimeError("EventDrivenScheduler: async loop не запустився за відведений час")

    async def stop(self) -> None:
        if self._async_loop and self._async_loop.is_running():
            # Graceful shutdown proxy_queue воркерів — перед зупинкою loop
            future = asyncio.run_coroutine_threadsafe(
                proxy_queue_manager.shutdown_all(), self._async_loop
            )
            try:
                future.result(timeout=15.0)
            except Exception as e:
                log.warning(f"proxy_queue shutdown error: {e}")

            # Оскільки BotSession та підключення сокетів ініціалізуються на домашньому (головному) loop,
            # закривати їх потрібно саме в ньому, а не в фоновому self._async_loop.
            with self._lock:
                entries = dict(self._containers)

            if entries:
                log.info("Disconnecting all accounts on home loop...")
                try:
                    # Виконуємо безпосередньо на поточному (home) loop
                    await asyncio.gather(
                        *(entry.bot.disconnect() for entry in entries.values()),
                        return_exceptions=True,
                    )
                except Exception as e:
                    log.warning(f"disconnect error during stop: {e}")

            self._async_loop.call_soon_threadsafe(self._async_loop.stop)

        if self._loop_thread and self._loop_thread.is_alive():
            self._loop_thread.join(timeout=10)

        log.info("EventDrivenScheduler stopped")

    def _run_async_loop(self) -> None:
        asyncio.set_event_loop(self._async_loop)
        self._loading_lock = asyncio.Lock()
        self._async_loop.run_forever()

    # ── Account management ────────────────────────────────────────────────────

    async def add_account(
        self,
        account_id: str,
        bot:        Account,
        guard:      Optional[Condition] = None,
    ) -> None:
        """
        Крок 1 з 3: відновлення акаунта з пам'яті.

        Реєструє контейнер і прив'язує CoreService-и (AuthService тощо).
        CoreService-и потрібні ДО connect() — вони реагують на on_auth_success
        під час першого authenticate().

        НЕ робить connect(), НЕ setup профессій.
        """
        if bot.status == AccountStatus.DEAD:
            if self._on_dead:
                self._on_dead(bot)
            return
        
        with self._lock:
            if account_id in self._containers:
                raise ValueError(f"Акаунт {account_id!r} вже існує")
            self._containers[account_id] = AccountContainer(bot=bot, guard=guard)

        await self._bind_core_services(bot)
        log.info(f"[{account_id}] зареєстровано")

    @staticmethod
    async def _bind_core_services(bot: Account) -> None:
        """
        Створює та прив'язує CoreService-и до акаунта.

        profession_registry.build_core_services() повертає свіжі екземпляри
        всіх зареєстрованих CoreService (по одному на акаунт).
        Кожен отримує посилання на bot через bind().
        """
        services = profession_registry.build_core_services()
        for svc in services:
            await svc.bind(bot)
        bot.core_services = services
        if services:
            log.debug(
                f"[{bot.account_id}] bound core services: "
                f"{[s.service_id for s in services]}"
            )

    async def connect_account(self, account_id: str) -> bool:
        """
        Крок 2 з 3: встановлює сесію.

        Тільки bot.connect(). Профессії і монітори — окремий крок (setup_professions).
        """
        with self._lock:
            container = self._containers.get(account_id)

        if container is None:
            log.warning(f"[{account_id}] connect_account: акаунт не знайдено")
            return False

        if container.bot.is_connected:
            log.debug(f"[{account_id}] connect_account: вже підключено")
            return True

        ok = await container.bot.connect()
        if not ok:
            log.error(f"[{account_id}] connect_account: connect() провалився — {container.bot.error}")
        return ok

    async def setup_professions(
        self,
        account_id:  str,
        professions: list["BaseProfession"],
    ) -> None:
        with self._lock:
            container = self._containers.get(account_id)

        if not container:
            return

        for profession in professions:
            await container.attach_profession(self, profession, self._router)

    async def add_profession_to_account(self, account_id: str, profession: "BaseProfession") -> None:
        with self._lock:
            container = self._containers.get(account_id)
            if not container:
                raise ValueError(f"Акаунт {account_id!r} не знайдено")
        
        await container.attach_profession(self, profession, self._router)

    async def remove_account(self, account_id: str) -> bool:
        with self._lock:
            container = self._containers.pop(account_id, None)
        if container is None:
            return False

        # Контейнер сам відпише професії, зачистить роутер і вимкне монітори
        await container.teardown_all(self, self._event_bus, self._router)
        self._router.unregister_account(account_id)

        for svc in container.bot.core_services:
            try:
                await svc.unbind()
            except Exception as e:
                log.warning(f"[{account_id}] CoreService {svc.service_id!r} unbind error: {e}")
        container.bot.core_services = []

        log.info(f"[{account_id}] видалено")
        return True

    async def pause_account(self, account_id: str) -> bool:
        with self._lock:
            container = self._containers.get(account_id)
        if container is None or container.bot.status == AccountStatus.SUSPENDED:
            return False

        await container.monitors.detach_all(self, self._event_bus)
        container.bot.status = AccountStatus.SUSPENDED
        await container.bot.disconnect()
        log.info(f"[{account_id}] призупинено")
        return True

    async def resume_account(self, account_id: str) -> bool:
        with self._lock:
            container = self._containers.get(account_id)
        if container is None or container.bot.status != AccountStatus.SUSPENDED:
            return False

        container.bot.status = AccountStatus.IDLE

        if not container.bot.is_connected:
            ok = await container.bot.connect()
            if not ok:
                return False

        if container.bot.status == AccountStatus.DEAD:
            return False

        # Контейнер сам перевірить, які монітори потрібні активним професіям
        await container.sync_monitors(self, self._event_bus)
        log.info(f"[{account_id}] відновлено")
        return True

    # ── Dynamic Profession Management ─────────────────────────────────────────

    async def remove_profession_from_account(self, account_id: str, profession_id: str) -> None:
        with self._lock:
            container = self._containers.get(account_id)
            if not container:
                return

        await container.detach_profession(self, profession_id, self._event_bus, self._router)

    # ── Public API ────────────────────────────────────────────────────────────

    def has_account(self, account_id: str) -> bool:
        with self._lock:
            return account_id in self._containers

    def has_profession(self, account_id: str, profession_id: str) -> bool:
        with self._lock:
            container = self._containers.get(account_id)
        return container.has_profession(profession_id) if container else False

    def get_bot(self, account_id: str) -> Optional[Account]:
        with self._lock:
            container = self._containers.get(account_id)
        return container.bot if container else None

    def get_container(self, account_id: str) -> Optional[AccountContainer]:
        with self._lock:
            return self._containers.get(account_id)

    def account_ids(self) -> list[str]:
        with self._lock:
            return list(self._containers.keys())

    def get_accounts_with_profession(self, profession_id: str) -> list[str]:
        with self._lock:
            return [
                account_id
                for account_id, container in self._containers.items()
                if container.has_profession(profession_id)
            ]

    async def dispatch_work(
        self,
        profession_id: str,
        intent:        str,
        items:         list[Any],
        item_key:      str,
        *,
        caller:        str   = "system",
        timeout:       float = 30.0,
    ) -> int:
        workers = self.get_accounts_with_profession(profession_id)
        if not workers:
            log.warning(
                f"dispatch_work: немає акаунтів з професією {profession_id!r} "
                f"— {len(items)} завдань не розподілено"
            )
            return 0

        chunks = _split_evenly(items, len(workers))

        dispatched = 0
        for account_id, chunk in zip(workers, chunks):
            if not chunk:
                continue
            await self.ask(
                account_id    = account_id,
                profession_id = profession_id,
                intent        = intent,
                data          = {item_key: chunk},
                caller        = caller,
                timeout       = timeout,
            )
            dispatched += 1

        return dispatched

    def status(self, account_id: str) -> Optional[AccountStatus]:
        with self._lock:
            entry = self._containers.get(account_id)
        return entry.bot.status if entry else None

    def all_statuses(self) -> dict[str, AccountStatus]:
        with self._lock:
            entries = dict(self._containers)
        return {aid: e.bot.status for aid, e in entries.items()}

    def profession_names(self, account_id: str) -> list[str]:
        with self._lock:
            entry = self._containers.get(account_id)
        if entry is None:
            return []
        return [p.profession_id for p in entry.profession_list()]

    # ── EventBus API ──────────────────────────────────────────────────────────

    def subscribe(self, event_name: str, callback: EventCallback) -> None:
        self._event_bus.subscribe(event_name, callback)

    async def try_acquire_loader_lock(self) -> bool:
        if self._loading_lock is None:
            return True
        if self._loading_lock.locked():
            return False
        await self._loading_lock.acquire()
        return True

    async def release_loader_lock(self) -> None:
        if self._loading_lock is not None and self._loading_lock.locked():
            self._loading_lock.release()

    async def emit_event(
        self,
        event_name: str,
        payload:    dict[str, Any],
        source:     str = "system",
    ) -> int:
        """
        ДУБЛЮВАННЯ-1 виправлено: єдиний метод emit_event — повноцінний await,
        повертає кількість успішних підписників.
        emit_event_async() видалено — він був надлишковим alias-ом.

        Автозбереження: якщо payload містить account_id — зберігає inventory
        після emit, щоб зміни зроблені монітором перед emit не пропали.
        """
        result = await self._event_bus.emit(event_name, payload, source=source)
        return result

    # ── RequestRouter API ─────────────────────────────────────────────────────

    async def ask(
        self,
        account_id: str,
        profession_id: str,
        intent: str,
        data: dict[str, Any] | None = None,
        *,
        caller: str = "admin",
        timeout: float = 60.0,
    ) -> "RequestResult":
        from src.core.runtime.profession import RequestResult

        bot = self.get_bot(account_id)
        if bot is None:
            return RequestResult.deny(f"account {account_id!r} not found")

        ctx = RequestContext(
            account_id    = account_id,
            profession_id = profession_id,
            intent        = intent,
            caller        = caller,
            _bot           = bot,
            timeout       = timeout,
        )
        return await self._router.route(ctx, data or {})