"""
core_account.py — модель акаунта + сесія.

Account не знає про Scheduler, Profession чи будь-яку event-bus —
socket-події (SocketService) і всі інші події тепер ідуть виключно
через ГЛОБАЛЬНИЙ scheduler-bus (EventDrivenScheduler.emit_event/
subscribe, на Redis) з фільтрацією по account_id у payload, за тим
самим патерном, що вже використовував DayAnnouncerService.

Account НЕ зберігає email/password/proxy — ці дані взагалі не тримаються
на боці core-service (ні в пам'яті, ні на диску, ні в .env). Єдине, що
core-service памʼятає про акаунт локально — сам `account_id` (+ email
для власної БД/логів, не секрет) і список профессій. Пароль/проксі
живуть ЛИШЕ на account-service, в його власній БД:

  - SchedulerService.add_account() один раз відправляє їх туди через
    account_client.register(...), коли адміністратор додає НОВИЙ акаунт
    або свідомо оновлює пароль/проксі.
  - Далі (перепідключення, рестарт core-service) Account.connect() лише
    просить account-service підняти сесію за вже відомим account_id —
    account_client.connect(account_id), без жодного пароля з нашого боку.

  connect()/disconnect() говорять з account-service через account_client
  (порт) — просять підключити/відключити сесію там. session property
  повертає легкий BotSession-фасад (bot_session.py), що для будь-якого
  бізнес-запиту звертається до account-service — самого HTTP/cookies/
  socket тут більше немає.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from src.core.account_client import account_client, AccountServiceError
from src.core.config.app import AppConfig
from src.core.inventory.model import DynamicInventories as Inventories
from src.core.stats import stats_factory, DynamicStats as Stats
from src.database.repository.factory import Repositories
from src.core.status import AccountStatus
from src.core.logging.loggers import get_account_logger

if TYPE_CHECKING:
    from src.mangabuff.session import BotSession
    from src.core.runtime.core_service import CoreService


class Account:
    def __init__(
        self,
        account_id: str,
        app_config: AppConfig,
        repo:       Repositories,
    ):
        self.account_id:   str           = account_id
        self.status:       AccountStatus = AccountStatus.IDLE
        self.error:        Optional[str] = None
        self.app_config:   AppConfig     = app_config
        self.repo:         Repositories  = repo
        self.inventories:  Inventories   = self.repo.inventory.load(self.account_id)
        self.recorder:     Stats         = stats_factory.build()

        # CoreService-и що автоматично прив'язані до цього акаунта.
        self.core_services: list["CoreService"] = []

        self._session:     Optional["BotSession"] = None
        self._log = get_account_logger(account_id)

    @property
    def inventory(self) -> Inventories:
        return self.inventories

    @property
    def session(self) -> Optional["BotSession"]:
        """
        Повертає активну сесію (легкий фасад, що ходить в account-service)
        або None якщо акаунт відключено.
        """
        return self._session

    @property
    def safe_session(self) -> "BotSession":
        if self._session is None:
            raise RuntimeError(
                f"[{self.account_id}] Сесія не встановлена. "
                "Переконайся що connect() був викликаний перед використанням сесії."
            )
        return self._session

    @property
    def is_connected(self) -> bool:
        return self._session is not None

    async def connect(self) -> bool:
        from src.mangabuff.session import BotSession
        from src.mangabuff.personal.auth_service import AuthService

        try:
            # Жодного пароля тут немає й не було — account-service вже
            # знає його з моменту SchedulerService.add_account() (там
            # був єдиний виклик account_client.register(...)). Тут лише
            # просимо підняти сесію за account_id.
            remote = await account_client.connect(self.account_id)

            if not remote.is_connected:
                return self._fail(remote.error or "Помилка підключення (account-service)")

            self._session = BotSession(
                self.account_id, account_client, self.app_config,
                user_id=remote.user_id, user_name=remote.user_name,
            )
            self.status = AccountStatus.IDLE
            self.error  = None

            # AuthService раніше отримував колбек після КОЖНОГО check_auth();
            # тепер account-service ре-логінить самостійно і не сповіщає про
            # кожен цикл — тут викликаємо разово, одразу після connect().
            for svc in self.core_services:
                if isinstance(svc, AuthService):
                    await svc.on_auth_success({
                        "user_id": remote.user_id,
                        "user_name": remote.user_name,
                        "is_banned": False,
                    })
                    break

            for svc in self.core_services:
                on_session_ready = getattr(svc, "on_session_ready", None)
                if on_session_ready is not None:
                    await on_session_ready(self)

            self._log.info("✅ Підключено (через account-service)")
            return True
        except AccountServiceError as e:
            return self._fail(f"account-service недоступний: {e}")
        except Exception as e:
            return self._fail(f"Помилка підключення: {e}")

    async def disconnect(self) -> None:
        if self._session:
            try:
                self.repo.inventory.save(self.account_id, self.inventories)
            except Exception as e:
                self._log.warning(f"Failed to save inventory on disconnect: {e}")

            for svc in self.core_services:
                on_session_closing = getattr(svc, "on_session_closing", None)
                if on_session_closing is not None:
                    await on_session_closing(self)

            await self._session.close()
            self._session = None
            self._log.info("🔌 Відключено")

    def mark_working(self) -> None:
        self.status = AccountStatus.WORKING

    def mark_idle(self) -> None:
        self.status = AccountStatus.IDLE

    def mark_dead(self, reason: str) -> None:
        self.status = AccountStatus.DEAD
        self.error  = reason
        self._log.critical(f"💀 {reason}")

    def _fail(self, reason: str) -> bool:
        if self.status == AccountStatus.DEAD:
            return False
        self.status = AccountStatus.ERROR
        self.error  = reason
        self._log.error(f"❌ {reason}")
        return False

    def __repr__(self) -> str:
        return (
            f"<Account id={self.account_id!r} "
            f"status={self.status.name} | "
            f"{self.inventories.personal}>"
        )
