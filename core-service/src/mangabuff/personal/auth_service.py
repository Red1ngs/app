"""
mangabuff/personal/auth_service.py — AuthService (CoreService).

Зберігає identity акаунта (user_name) у PersonalInventory після авторизації.

Чому CoreService, а не BaseProfession:
    AuthService — не «режим роботи» акаунта. Він не обирається адміном,
    не зберігається в БД, не обробляє scheduler.ask(). Він завжди активний
    для кожного акаунта — інфраструктурна деталь, невидима назовні.

Як підключається:
    В mangabuff/setup.py:
        profession_registry.add_core_service(AuthService)

    ProfessionRegistry.build_core_services() викликається scheduler при
    add_account() — один AuthService на акаунт.

Як взаємодіє з account-service:
    scheduler (в connect_account) передає auth_service.on_auth_success
    як колбек у Account. Після успішного connect() до account-service
    Account.connect() викликає цей колбек з user_name/user_id.

Доступ до user_name звідусіль:
    bot.inventory.personal.user_name
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core.runtime.core_service import CoreService
from src.core.logging.loggers import get_logger

if TYPE_CHECKING:
    from src.core.core_account import Account

log = get_logger("service.auth")


class AuthService(CoreService):
    """
    Зберігає user_name у PersonalInventory після кожної успішної авторизації.

    Єдиний публічний метод окрім lifecycle — on_auth_success(user_name),
    що передається як AuthSuccessCallback у BotSession.
    """

    def __init__(self) -> None:
        self._account: Account | None = None
        
    @property
    def service_id(self) -> str:
        return "auth"

    # ── CoreService lifecycle ─────────────────────────────────────────────────

    async def bind(self, bot: "Account") -> None:
        """
        Прив'язує сервіс до акаунта.

        Зберігаємо посилання; якщо user_name вже є в inventory
        (відновлення після рестарту) — логуємо.
        """
        self._account = bot
        if bot.inventory.personal.user_name:
            log.info(
                f"[{bot.account_id}] identity restored from inventory: "
                f"{bot.inventory.personal.user_name!r}"
            )
            
        if bot.inventory.personal.user_id:
            log.info(
                f"[{bot.account_id}] user_id restored from inventory: "
                f"{bot.inventory.personal.user_id!r}"
            )

    async def unbind(self) -> None:
        self._account = None

    # ── AuthSuccessCallback ───────────────────────────────────────────────────

    async def on_auth_success(self, data: dict[str, Any]) -> None:
        """
        Викликається Account.connect() після успішного підключення
        до account-service.

        Зберігає user_name у PersonalInventory і одразу персистує в БД.
        Персистенція тут явна (не через RequestRouter) бо on_auth_success
        викликається поза lifecycle handle_request.
        """
        user_name: str | None = data.get("user_name")
        user_id: str | None = data.get("user_id")
        is_banned: bool = data.get("is_banned", False)
        
        if self._account is None:
            # bind() ще не викликався. Таке можливо якщо on_auth_success
            # спрацьовує під час самого connect() раніше ніж bind завершився.
            # Безпечно ігнорувати — user_name буде збережений при наступному
            # логіні після того як bind() вже викличеться.
            log.debug(
                f"on_auth_success({user_name!r}, {user_id!r}, {is_banned!r}): not yet bound to account, skipping"
            )
            return

        bot = self._account
        bot.inventory.personal.user_name = user_name
        bot.inventory.personal.user_id = user_id
        bot.inventory.personal.is_banned = is_banned
        log.info(f"[{bot.account_id}] identity saved: {user_name!r} (ID: {user_id!r})")

        try:
            bot.repo.inventory.save(bot.account_id, bot.inventory)
        except Exception as e:
            log.warning(f"[{bot.account_id}] failed to persist user identity: {e}")