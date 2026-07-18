"""
core_account.py — модель акаунта + сесія.

Account не знає про Scheduler, Profession чи EventBus (крім самого
event_bus, який належить йому).

Зміна відносно попередньої версії (винесення account-service):
  - Account більше НЕ будує BotSession/BotHttpClient/BotSocket сам.
    connect()/disconnect() тепер лише говорять з account-service через
    account_client (порт) — реєструють облікові дані (email/password/proxy)
    і просять підключити/відключити сесію там.
  - session property повертає легкий BotSession-фасад (bot_session.py),
    що для будь-якого бізнес-запиту звертається до account-service —
    самого HTTP/cookies/socket тут більше немає.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from src.core.account_client import account_client, AccountServiceError
from src.core.config.app import AppConfig
from src.core.config.bot import AuthConfig, NetworkConfig
from src.core.inventory.model import DynamicInventories as Inventories
from src.core.stats import stats_factory, DynamicStats as Stats
from src.database.repository.factory import Repositories
from src.core.status import AccountStatus
from src.core.logging.loggers import get_account_logger
from src.core.runtime.event_bus import EventBus

if TYPE_CHECKING:
    from src.mangabuff.session import BotSession
    from src.core.runtime.core_service import CoreService


class Account:
    def __init__(
        self,
        account_id: str,
        auth:       AuthConfig,
        network:    NetworkConfig,
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

        # Персональна шина подій акаунта. SocketService ретранслює сюди
        # socket-події, отримані через account_events (Redis) з
        # account-service. Profession підписується через scheduler.subscribe().
        self.event_bus:    EventBus      = EventBus()

        # CoreService-и що автоматично прив'язані до цього акаунта.
        self.core_services: list["CoreService"] = []

        self._network:     NetworkConfig = network
        self._auth:        AuthConfig    = auth
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
            # Ідемпотентно: реєструє/оновлює облікові дані на account-service.
            # Пароль/проксі лишаються джерелом правди в бізнес-БД/.env — сюди
            # передаються лише щоб account-service міг залогінитись сам.
            await account_client.register(
                self.account_id,
                email=self._auth.email,
                password=self._auth.password,
                proxy=self._network.proxy,
            )
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
