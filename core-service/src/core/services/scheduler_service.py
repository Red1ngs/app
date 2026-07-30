"""
core/services/scheduler_service.py

Тонкий фасад над Scheduler-сінглтоном — ЄДИНА публічна поверхня бізнес-логіки.

Раніше викликався лише з адмін-бота, що жив в одному процесі (окремий
потік). Тепер бот винесений у власний сервіс (`telegram-service`,
сиблінг-директорія в корені репо) і звертається сюди виключно через
generic RPC (`src/core/rpc/server.py`, `POST /rpc/{method}`) — жодних
прямих імпортів `SchedulerService` за межами цього процесу. Саме тому
кожен метод тут повертає ТІЛЬКИ JSON-серіалізовний результат (str, bool,
int, list, dict, tuple, frozen-dataclass), а не живі об'єкти (`Account`,
`BotSession` тощо) — RPC-шар не вміє і не повинен їх серіалізувати.

Пароль/проксі акаунтів core-service НІДЕ не зберігає (ні в пам'яті, ні в
.env, ні в БД) — джерело правди одне: account-service, і саме туди
add_account() один раз відправляє їх через account_client.register(...).
Локально (repo.accounts) лишається тільки account_id + email (не секрет)
для власного обліку/логів і щоб при рестарті було що передати
register_account() — сам пароль при рестарті не потрібен узагалі:
Account.connect() лише просить account-service підняти сесію за вже
відомим account_id (див. core_account.py).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, TypeVar

from src.core.account_client import account_client, AccountServiceError, AccountNotFoundError
from src.core.config.app import AppConfig
from src.core.runtime.scheduler import EventDrivenScheduler
from src.core.core_account import Account
from src.core.runtime.profession import BaseProfession
from src.core.runtime.profession_spec import profession_registry
from src.core.logging.reader import LogReader
from src.database.repository.factory import Repositories
from src.core.logging.loggers import get_logger

log = get_logger("core.scheduler_service")

T = TypeVar("T")


# ─────────────────────────────────────────────────────────────────────────────
# DTO
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AccountInfo:
    account_id:   str
    email:        str
    proxy:        str
    status:       str
    mangabuff:    MangabuffInfo
    queue_size:   int = 0
    professions:  list[str] = field(default_factory=list[str])
    monitors:     list[str] = field(default_factory=list[str])
    is_connected: bool = False
    # Стан circuit breaker'а проксі на account-service: True, якщо
    # CLOSED/HALF_OPEN (запити йдуть), False, якщо OPEN (fast-fail) або
    # health взагалі недоступний. None, якщо акаунт не підключений і
    # health не питали. Раніше ця інформація існувала лише всередині
    # account-service і не була видима нізвідки, крім грепу логів.
    circuit_healthy: Optional[bool] = None

    @property
    def profession(self) -> Optional[str]:
        return self.professions[0] if self.professions else None
    
    
@dataclass(frozen=True)
class MangabuffInfo:
    user_name:    str
    user_id:      str


@dataclass(frozen=True)
class SchedulerSnapshot:
    total_accounts: int
    accounts:       list[AccountInfo]


# ─────────────────────────────────────────────────────────────────────────────
# SchedulerService
# ─────────────────────────────────────────────────────────────────────────────

class SchedulerService:
    def __init__(self, repo: Repositories, app_config: AppConfig) -> None:
        self._repo       = repo
        self._app_config = app_config
        self._log_reader = LogReader()

    @property
    def _scheduler(self) -> EventDrivenScheduler:
        return EventDrivenScheduler.get_instance()

    # ── Безпечний міст між потоками/loop'ами ────────────────────────────────

    async def _run_on_home_loop(self, factory: Callable[[], "Awaitable[T]"]) -> T:
        """
        Гарантує, що корутина, яка торкається Account / BotSession
        (а отже — curl_cffi.AsyncSession, прив'язаної до КОНКРЕТНОГО event
        loop'у з моменту створення), завжди виконується саме в тому loop'і,
        де живе scheduler ("домашній" loop, зафіксований у
        EventDrivenScheduler.initialize()).

        Без цього мосту викликач з admin-bot потоку (AdminBotRunner._run() —
        окремий threading.Thread зі своїм asyncio.new_event_loop()) виконував
        би scheduler.xxx() прямим await'ом у СВОЄМУ loop'і. Якщо саме звідти
        піде перший реальний HTTP-запит (наприклад, при hot-add профессії),
        curl_cffi впаде з RuntimeError "Future attached to a different loop",
        бо AsyncSession створено в іншому, головному loop'і.

        factory — функція БЕЗ аргументів, що повертає СВІЖИЙ awaitable.
        Не передавай вже створену корутину напряму: вона може знадобитись
        двічі (локальний await vs run_coroutine_threadsafe), а корутину
        можна запустити лише один раз.

        Приклад використання:
            return await self._run_on_home_loop(
                lambda: self._scheduler.connect_account(account_id)
            )
        """
        home_loop = self._scheduler.home_loop
        current_loop = asyncio.get_running_loop()

        if home_loop is None or current_loop is home_loop:
            # Ми вже там, де і має бути (типовий випадок — StartupManager,
            # або scheduler ще не запущений і home_loop невідомий: тоді
            # просто виконуємо як є, бо переносити нікуди).
            return await factory()

        # Викликано з чужого loop'у (admin-bot потік, тести тощо) —
        # плануємо корутину в домашньому loop'і й чекаємо результат тут,
        # НЕ блокуючи поточний loop синхронним .result().
        concurrent_future = asyncio.run_coroutine_threadsafe(factory(), home_loop)
        return await asyncio.wrap_future(concurrent_future)

    # ── Читання стану ─────────────────────────────────────────────────────────

    async def snapshot(self) -> SchedulerSnapshot:
        return await self._run_on_home_loop(lambda: self._snapshot_impl())

    async def _snapshot_impl(self) -> SchedulerSnapshot:
        scheduler = self._scheduler
        accounts = [
            info
            for acc_id in scheduler.account_ids()
            if (info := await self._build_info(acc_id, scheduler)) is not None
        ]
        return SchedulerSnapshot(total_accounts=len(accounts), accounts=accounts)

    async def account_info(self, account_id: str) -> Optional[AccountInfo]:
        return await self._run_on_home_loop(
            lambda: self._build_info(account_id, self._scheduler)
        )

    async def _build_info(self, acc_id: str, scheduler: EventDrivenScheduler) -> Optional[AccountInfo]:
        container = scheduler.get_container(acc_id)
        status = scheduler.status(acc_id)
        if container is None or status is None:
            return None

        profs = scheduler.profession_names(acc_id)
        is_connected = container.bot.is_connected

        # Раніше тут був безумовний container.bot.safe_session..., що
        # кидає RuntimeError, якщо акаунт не підключений (наприклад,
        # проксі мертве чи авторизація не пройшла на старті). Через
        # list comprehension у _snapshot_impl() ОДИН такий акаунт валив
        # snapshot() ЦІЛКОМ — жоден акаунт не показувався, поки саме цей
        # не полагодять (див. traceback [rpc] snapshot failed: [Asukaaa]
        # Сесія не встановлена). Тепер для непідключених акаунтів просто
        # не питаємо auth/health — показуємо доступну інфу з локального
        # стану (email/proxy будуть "—", але сам акаунт лишається видимим
        # у списку разом з error).
        email = "—"
        proxy = "—"
        circuit_healthy: Optional[bool] = None
        if is_connected:
            try:
                auth = await container.bot.safe_session.account_client.get_status(acc_id)
                if auth:
                    email = auth.email
                    proxy = auth.proxy or "—"
            except Exception as e:
                log.warning(f"[{acc_id}] snapshot: не вдалося отримати auth-статус: {e}")

            try:
                health = await account_client.get_health(acc_id)
                circuit_healthy = health.healthy
            except Exception as e:
                log.debug(f"[{acc_id}] snapshot: health недоступний: {e}")

        active_monitors = []
        
        am = container.monitors
        active_monitors = am.active_ids()

        user_name = container.bot.inventory.personal.user_name or "—"
        user_id = container.bot.inventory.personal.user_id or "—"
        buff_info = MangabuffInfo(
            user_name=user_name,
            user_id=user_id
        )
        
        return AccountInfo(
            account_id       = acc_id,
            email            = email,
            proxy            = proxy if proxy else "—",
            status           = status.name,
            mangabuff        = buff_info,
            queue_size       = 0,
            professions      = profs,
            monitors         = active_monitors,
            is_connected     = is_connected,
            circuit_healthy  = circuit_healthy,
        )

    async def account_ids(self) -> list[str]:
        return await self._run_on_home_loop(lambda: self._account_ids_impl())

    async def _account_ids_impl(self) -> list[str]:
        return self._scheduler.account_ids()

    async def get_bot(self, account_id: str):
        """
        Повертає Account або None. Використовується ЛИШЕ StartupManager
        (той самий процес, той самий loop). НЕ виставляється в RPC —
        Account не серіалізовний. Зовнішнім викликачам (telegram-service)
        потрібне лише `.error` — див. `get_account_error()` нижче.
        """
        return await self._run_on_home_loop(lambda: self._get_bot_impl(account_id))

    async def _get_bot_impl(self, account_id: str):
        return self._scheduler.get_bot(account_id)

    async def get_account_error(self, account_id: str) -> Optional[str]:
        """
        RPC-безпечна заміна `(await get_bot(id)).error` — повертає лише
        текст останньої помилки (або None, якщо акаунта немає чи помилки
        немає), без живого об'єкта Account.
        """
        bot = await self._run_on_home_loop(lambda: self._get_bot_impl(account_id))
        return getattr(bot, "error", None) if bot else None

    async def find_account_by_email(self, email: str) -> Optional[str]:
        """
        RPC-безпечна заміна прямого `svc._repo.accounts.get_by_email(email)` —
        повертає account_id власника email або None.
        """
        existing = self._repo.accounts.get_by_email(email)
        return existing.id if existing else None

    # ── Логи (для /logs у telegram-service) ─────────────────────────────────

    def logs_list_accounts(self) -> list[str]:
        return self._log_reader.list_accounts()

    def logs_tail_account(self, account_id: str, n: int = 40) -> list[str]:
        return self._log_reader.tail_account(account_id, n)

    def logs_tail_scheduler(self, n: int = 40) -> list[str]:
        return self._log_reader.tail_scheduler(n)

    def logs_errors(self, since_hours: float = 24) -> list[str]:
        return self._log_reader.errors(since_hours=since_hours)

    # ── Метадані (для меню "додати професію" у telegram-service) ───────────

    def known_professions(self) -> list[str]:
        return profession_registry.known_ids()

    async def connect_account(self, account_id: str) -> bool:
        """
        Встановлює сесію і підключає монітори.
        Делегує в scheduler.connect_account() — єдине місце де це відбувається.
        Завжди виконується в домашньому loop'і scheduler'а (див. _run_on_home_loop).
        """
        return await self._run_on_home_loop(
            lambda: self._scheduler.connect_account(account_id)
        )

    async def account_status(self, account_id: str) -> Optional[str]:
        """
        RPC-безпечна назва статусу акаунта (AccountStatus.name, напр. "ERROR",
        "DEAD", "IDLE") або None, якщо акаунта немає. Використовується
        ReconnectWatchdog-ом (щоб не тягнути живий Account через RPC).
        """
        return await self._run_on_home_loop(lambda: self._account_status_impl(account_id))

    async def _account_status_impl(self, account_id: str) -> Optional[str]:
        status = self._scheduler.status(account_id)
        return status.name if status else None

    async def reconnect_account(self, account_id: str) -> bool:
        """
        Повторна спроба підключення акаунта, що впав у ERROR — механізм,
        яким користується ReconnectWatchdog (і яким можна скористатись
        вручну, напр. з телеграм-бота, для миттєвого ретраю).

        На відміну від голого connect_account(), після успіху довстановлює
        професії, якщо вони ще не прикріплені: attach_profession()
        ідемпотентний (AccountContainer.attach_profession — пропускає вже
        зареєстровані), тож безпечно викликати повторно навіть якщо частина
        професій вже була приєднана до падіння в ERROR.
        """
        return await self._run_on_home_loop(
            lambda: self._reconnect_account_impl(account_id)
        )

    async def _reconnect_account_impl(self, account_id: str) -> bool:
        scheduler = self._scheduler
        if not scheduler.has_account(account_id):
            return False

        if not await scheduler.connect_account(account_id):
            return False

        professions = self._build_professions(account_id)
        await scheduler.setup_professions(account_id, professions)
        return True

    async def problem_accounts(self) -> list[dict[str, Any]]:
        """
        RPC-безпечний список акаунтів, що потребують уваги: ERROR
        (ReconnectWatchdog вже намагається сам, але варто знати, якщо це
        затягнулось) і DEAD (забанені/непідключні — самі себе не
        відновлять, потрібне ручне втручання). Використовується
        telegram-service для періодичних алертів адмінам — і будь-яким
        іншим монітором (напр. /health/accounts у rpc/server.py).
        """
        return await self._run_on_home_loop(lambda: self._problem_accounts_impl())

    async def _problem_accounts_impl(self) -> list[dict[str, Any]]:
        scheduler = self._scheduler
        result: list[dict[str, Any]] = []
        for account_id in scheduler.account_ids():
            status = scheduler.status(account_id)
            if status is None or status.name not in ("ERROR", "DEAD"):
                continue
            bot = scheduler.get_bot(account_id)
            result.append({
                "account_id": account_id,
                "status": status.name,
                "error": getattr(bot, "error", None),
            })
        return result

    async def disconnect_account(self, account_id: str) -> bool:
        """Закриває сесію акаунта без зупинки профессій."""
        return await self._run_on_home_loop(
            lambda: self._disconnect_account_impl(account_id)
        )

    async def _disconnect_account_impl(self, account_id: str) -> bool:
        bot = self._scheduler.get_bot(account_id)
        if bot is None:
            return False
        await bot.disconnect()
        return True

    # ── Створення акаунта ─────────────────────────────────────────────────────

    def _build_professions(self, account_id: str) -> list[BaseProfession]:
        """Будує список профессій акаунта з БД. Дедублікує deps."""
        db_acc = self._repo.accounts.get(account_id)
        names  = db_acc.professions if db_acc else []
        seen: set[str] = set()
        result: list[BaseProfession] = []
        for name in names:
            try:
                for p in profession_registry.build(name):
                    if p.profession_id not in seen:
                        seen.add(p.profession_id)
                        result.append(p)
            except Exception as e:
                log.warning(f"[{account_id}] Cannot build profession {name!r}: {e}")
        return result

    async def _register(self, account_id: str, email: str) -> tuple[bool, str]:
        """
        Єдине місце створення акаунта: перевірка → bot → scheduler.add_account().
        Крок 1 з 3; connect і setup — відповідальність викликача.
        Виконується в домашньому loop'і scheduler'а.
        """
        return await self._run_on_home_loop(lambda: self._register_impl(account_id, email))

    async def _register_impl(self, account_id: str, email: str) -> tuple[bool, str]:
        if self._scheduler.has_account(account_id):
            return False, f"Акаунт {account_id!r} вже існує"

        bot = Account(account_id, self._app_config, self._repo)

        try:
            await self._scheduler.add_account(account_id, bot)
        except ValueError as e:
            return False, str(e)

        return True, ""

    async def register_account(self, account_id: str, email: str) -> tuple[bool, str]:
        """Реєстрація без connect. StartupManager далі робить кроки 2-3."""
        return await self._register(account_id, email)

    async def add_account(
        self,
        account_id: str,
        email:      str,
        password:   str = "",
        proxy:      str = "",
    ) -> tuple[bool, str]:
        """
        Hot-add: якщо передано пароль — реєструє/оновлює облікові дані
        ОДИН РАЗ напряму на account-service (єдине джерело правди для
        password/proxy), потім усі три кроки (register/connect/setup).

        Нічого із секретів тут НЕ зберігається локально — ні в .env, ні
        в БД. Локально (repo.accounts) лишається лише account_id + email.
        """
        if password:
            try:
                await self._run_on_home_loop(lambda: account_client.register(
                    account_id, email=email, password=password, proxy=proxy or None,
                ))
            except AccountServiceError as e:
                return False, f"account-service недоступний: {e}"
            try:
                self._repo.accounts.upsert(account_id, email, professions=None)
            except ValueError as e:
                # Email вже зайнято іншим аккаунтом
                return False, str(e)

        ok, err = await self._register(account_id, email)
        if not ok:
            return False, err

        return await self._run_on_home_loop(
            lambda: self._add_account_finish_impl(account_id)
        )

    async def _add_account_finish_impl(self, account_id: str) -> tuple[bool, str]:
        scheduler = self._scheduler
        bot = scheduler.get_bot(account_id)

        if not await scheduler.connect_account(account_id):
            await scheduler.remove_account(account_id)
            return False, f"Сесія не встановлена: {(bot and bot.error) or 'connect() повернув False'}"

        await scheduler.setup_professions(account_id, self._build_professions(account_id))
        return True, ""

    async def update_account_data(
        self,
        account_id: str,
        email: Optional[str] = ...,
        proxy: Optional[str] = ...,
    ) -> tuple[bool, str]:
        """
        Вільна зміна email/проксі "на ходу" — без ручного
        disconnect/patch/connect на боці викликача: account-service сам
        перепідключить живу сесію, якщо proxy справді змінюється (email
        сесії не стосується, для нього перепідключення не потрібне).

        Аргумент, який не треба чіпати, просто не передається (RPC-шар
        кладе в kwargs лише те, що явно попросив викликач) — тоді тут
        лишається дефолт `...` і ми його ігноруємо. proxy=None прибирає
        проксі (працювати напряму, без нього); email=None недопустимий.
        """
        if email is ... and proxy is ...:
            return False, "Не передано жодного поля для зміни"

        kw: dict[str, Any] = {}
        if email is not ...:
            if not email:
                return False, "email не може бути порожнім"
            kw["email"] = email
        if proxy is not ...:
            kw["proxy"] = proxy

        try:
            await self._run_on_home_loop(lambda: account_client.update_account(account_id, **kw))
        except AccountServiceError as e:
            return False, f"account-service: {e}"

        if email is not ... and email:
            # Локальна копія email (лише для власного обліку/логів —
            # джерело правди лишається на account-service, див. коментар
            # на початку файлу).
            try:
                self._repo.accounts.upsert(account_id, email, professions=None)
            except ValueError as e:
                return False, str(e)

        return True, ""

    # ── Управління professions ────────────────────────────────────────────────

    async def add_profession(
        self,
        account_id:    str,
        profession_name: str,
        *,
        priority: int = -1,
    ) -> tuple[bool, str]:
        ok, err = await self._run_on_home_loop(
            lambda: self._add_profession_impl(account_id, profession_name)
        )
        if ok:
            # В БД зберігаємо тільки сам вибраний profession_name, не deps
            self._repo.accounts.add_profession(account_id, profession_name, priority=priority)
        return ok, err

    async def _add_profession_impl(
        self, account_id: str, profession_name: str
    ) -> tuple[bool, str]:
        scheduler = self._scheduler
        if not scheduler.has_account(account_id):
            return False, f"Акаунт {account_id!r} не знайдено"

        if scheduler.has_profession(account_id, profession_name):
            return True, ""

        try:
            # build() повертає [dep1, dep2, ..., profession] — додаємо всі
            to_add = profession_registry.build(profession_name)
        except Exception as e:
            return False, f"Помилка збірки profession {profession_name!r}: {e}"

        for profession in to_add:
            if scheduler.has_profession(account_id, profession.profession_id):
                continue  # dep вже є — пропускаємо
            try:
                await scheduler.add_profession_to_account(account_id, profession)
            except Exception as e:
                return False, f"Не вдалося додати {profession.profession_id!r}: {e}"

        return True, ""

    async def remove_profession(
        self,
        account_id:      str,
        profession_name: str,
    ) -> tuple[bool, str]:
        ok, err = await self._run_on_home_loop(
            lambda: self._remove_profession_impl(account_id, profession_name)
        )
        if ok:
            self._repo.accounts.remove_profession(account_id, profession_name)
        return ok, err

    async def _remove_profession_impl(
        self, account_id: str, profession_name: str
    ) -> tuple[bool, str]:
        scheduler = self._scheduler
        if not scheduler.has_account(account_id):
            return False, f"Акаунт {account_id!r} не знайдено"

        await scheduler.remove_profession_from_account(account_id, profession_name)
        return True, ""

    async def set_professions(
        self,
        account_id:  str,
        profession_names: list[str],
    ) -> tuple[bool, str]:
        ok, err = await self._run_on_home_loop(
            lambda: self._set_professions_impl(account_id, profession_names)
        )
        if ok:
            target = list(dict.fromkeys(profession_names))
            self._repo.accounts.set_professions(account_id, target)
        return ok, err

    async def _set_professions_impl(
        self, account_id: str, profession_names: list[str]
    ) -> tuple[bool, str]:
        scheduler = self._scheduler
        if not scheduler.has_account(account_id):
            return False, f"Акаунт {account_id!r} не знайдено"

        current = set(scheduler.profession_names(account_id))
        target  = list(dict.fromkeys(profession_names))

        for name in current - set(target):
            await scheduler.remove_profession_from_account(account_id, name)

        for name in target:
            if not scheduler.has_profession(account_id, name):
                try:
                    to_add = profession_registry.build(name)
                    for profession in to_add:
                        if not scheduler.has_profession(account_id, profession.profession_id):
                            await scheduler.add_profession_to_account(account_id, profession)
                except Exception as e:
                    return False, f"Помилка profession {name!r}: {e}"

        return True, ""

    # ── Видалення акаунта ─────────────────────────────────────────────────────

    async def remove(self, account_id: str) -> bool:
        """
        ЄДИНА точка повного видалення акаунта. Викликається виключно з
        telegram-service (адмін підтвердив видалення) — жоден інший сервіс
        сам видалення не ініціює.

        Прибирає акаунт УСЮДИ, а не лише з живого scheduler'а:
          1) знімає з live-scheduler'а (сесія, професії, монітори) —
             попутно DayAnnouncerService.unbind() сам відпише акаунт від
             day-service (fire-and-forget day_client.unregister());
          2) видаляє облікові дані (email/пароль/проксі/сесія) на
             account-service — раніше це свідомо не робилось, і акаунти
             назавжди лишались висіти там навіть після видалення тут;
          3) видаляє локальний рядок (id/email/professions) із власної БД
             core-service.

        Крок 2 — best-effort: якщо account-service тимчасово недоступний,
        локальне видалення (1, 3) все одно доводиться до кінця, а не
        зависає в невизначеному стані; помилка лише логується.
        """
        # Кожен крок виконується незалежно від успіху попередніх: акаунт
        # може існувати в live-scheduler'і, але вже бути видаленим на
        # account-service (чи навпаки) — часткова неузгодженість якраз і є
        # тим станом, який ця функція покликана виправляти, тож не
        # виходимо раніше часу.
        was_live = await self._run_on_home_loop(lambda: self._scheduler.remove_account(account_id))

        account_service_deleted = False
        try:
            await self._run_on_home_loop(lambda: account_client.delete_account(account_id))
            account_service_deleted = True
        except AccountNotFoundError:
            pass  # вже видалено раніше — не помилка
        except AccountServiceError as e:
            log.warning(f"[{account_id}] не вдалось видалити на account-service: {e}")

        try:
            local_row_deleted = self._repo.accounts.delete(account_id)
        except Exception as e:
            log.warning(f"[{account_id}] не вдалось видалити локальний рядок: {e}")
            local_row_deleted = False

        return was_live or account_service_deleted or local_row_deleted

    # ── Async операції ────────────────────────────────────────────────────────

    async def force_parse_mangas(
        self,
        account_id: str,
        targets: list[str],
    ) -> tuple[bool, str, dict[str, Any]]:
        res = await self._run_on_home_loop(lambda: self._scheduler.ask(
            account_id,
            profession_id="manga_loader",
            intent="force_parse",
            data={"translits": targets},
        ))
        if res.approved:
            data = res.data or {}
            return True, "", {
                "chapters": data.get("chapters_saved", 0),
                "mangas":   data.get("mangas", 0),
            }
        return False, res.reason or "невідома помилка", {}

    async def mark_mangas_read(
        self,
        account_id: str,
        targets: list[str],
    ) -> tuple[bool, str, dict[str, Any]]:
        res = await self._run_on_home_loop(lambda: self._scheduler.ask(
            account_id,
            profession_id="reader",
            intent="mark_read",
            data={"targets": targets},
        ))
        if res.approved:
            return True, "", res.data or {}
        return False, res.reason or "невідома помилка", {}

    async def update_reading_params(
        self,
        account_id:   str,
        limit:        int                  = 2,
        include_tags: Optional[list[str]]  = None,
        exclude_tags: Optional[list[str]]  = None,
    ) -> bool:
        res = await self._run_on_home_loop(lambda: self._scheduler.ask(
            account_id,
            profession_id="reader",
            intent="set_reading_params",
            data={
                "limit":        limit,
                "include_tags": include_tags,
                "exclude_tags": exclude_tags,
            },
        ))
        if res.approved:
            return True
        return False

    async def reset_catalog_page(self, account_id: str) -> tuple[bool, str]:
        res = await self._run_on_home_loop(lambda: self._scheduler.ask(
            account_id,
            profession_id="manga_loader",
            intent="reset_catalog_page",
            data={},
        ))
        if res.approved:
            return True, ""
        return False, res.reason or "невідома помилка"

    async def get_reader_state(self, account_id: str) -> tuple[bool, dict[str, Any]]:
        res = await self._run_on_home_loop(lambda: self._scheduler.ask(
            account_id,
            profession_id="reader",
            intent="get_state",
            data={},
        ))
        if res.approved:
            return True, res.data or {}
        return False, {}

    async def pause(self, account_id: str)  -> bool:
        return await self._run_on_home_loop(lambda: self._scheduler.pause_account(account_id))

    async def resume(self, account_id: str) -> bool:
        return await self._run_on_home_loop(lambda: self._scheduler.resume_account(account_id))