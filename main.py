"""
main.py — точка входу.

При старті завантажує .env (паролі акаунтів, токен бота, admin ids).
Акаунти відновлюються з БД і підключаються послідовно (StartupManager),
щоб уникнути паралельного флуду login-запитів і помилки "Сесія не встановлена".
"""
import asyncio
import os
from pathlib import Path

# ── Завантаження .env ─────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(".env"), override=False)
except ImportError:
    pass

from src.core.logging.setup import setup_logging
setup_logging(log_dir="logs", console=True)

from src.core.logging.loggers import get_scheduler_logger
log = get_scheduler_logger()
log.info("=" * 60)
log.info("Application starting")

# ── Реєстрація ────────────────────────────────────────────────────────────────
from src.mangabuff.setup import bootstrap

bootstrap()

# ── БД ────────────────────────────────────────────────────────────────────────
from src.database.setup import init_database
Path("data").mkdir(exist_ok=True)
repositories = init_database("data/bot_state.db")

# ── AppConfig ─────────────────────────────────────────────────────────────────
from src.core.config.app import AppConfig
app_cfg = AppConfig.from_yaml("app.yaml")

# ── Часова зона ───────────────────────────────────────────────────────────────
from src.utils.time import set_timezone
set_timezone("Europe/Kiev")

# ── Scheduler ─────────────────────────────────────────────────────────────────
from src.core.core_account import Account
from src.core.runtime.scheduler import EventDrivenScheduler

# ── Services ──────────────────────────────────────────────────────────────────
from src.core.services.scheduler_service import SchedulerService
from src.database.repository.account import AccountRepository
from src.core.runtime.startup_manager import StartupManager, StartupConfig


async def restore_accounts(
    service:     SchedulerService,
    startup_cfg: StartupConfig,
    repository:  AccountRepository,
) -> None:
    """
    Крок 1 — register_account() для всіх акаунтів з БД (без connect, без профессій).
    Крок 2 — StartupManager послідовно: connect_account() → setup_professions().
    """
    registered: list[str] = []

    for row in repository.get_all_accounts():
        ok, err = await service.register_account(row.id, row.email)
        if not ok:
            log.warning(f"[restore] '{row.id}' пропущено: {err}")
            continue
        if not row.professions:
            log.warning(f"[restore] '{row.id}' без profession — моніторів не буде")
        registered.append(row.id)

    if not registered:
        log.info("[restore] Немає акаунтів для відновлення")
        return

    sm = StartupManager(service=service, cfg=startup_cfg)
    for aid in registered:
        sm.add(aid)
    await sm.run()

    if sm.failed_accounts:
        log.warning(
            "[restore] Не підключились: "
            + ", ".join(f"'{a}' ({e})" for a, e in sm.failed_accounts)
        )


# ── RPC-сервер + main loop ────────────────────────────────────────────────────
# Адмінський Telegram-бот більше НЕ тут — він винесений у власний сервіс
# `telegram-service` (сиблінг-директорія `../telegram_service`), який
# звертається сюди виключно через generic HTTP RPC (`src/core/rpc/server.py`).
# Цей процес лишається "безголовим": жодного знання про Telegram/aiogram.
import uvicorn
from src.core.rpc.server import create_rpc_app

RPC_HOST = os.environ.get("CORE_SERVICE_HOST", "0.0.0.0")
RPC_PORT = int(os.environ.get("CORE_SERVICE_PORT", "8200"))


async def main() -> None:
    def on_dead(bot: Account) -> None:
        log.critical(f"[DEAD] '{bot.account_id}': {bot.error}")

    # Слухач socket-подій з account-service (Redis) — має стартувати ДО
    # відновлення акаунтів, бо SocketService підписується вже в bind().
    from src.core.account_events import account_event_bus
    await account_event_bus.start()

    # Слухач "новий день настав" з day-service (Redis) — так само має
    # стартувати ДО відновлення акаунтів, бо DayAnnouncerService
    # підписується вже в bind() (той самий момент, що SocketService вище).
    from src.core.day_events import day_event_bus
    await day_event_bus.start()

    scheduler = await EventDrivenScheduler.initialize(on_dead=on_dead)
    log.info("Scheduler initialized (empty)")

    scheduler.start()
    
    startup_cfg = StartupConfig.from_app_config(app_cfg)
    svc = SchedulerService(repositories, app_cfg)

    await restore_accounts(svc, startup_cfg, repositories.accounts)

    # RPC-сервер — єдина точка входу ззовні (замінює колишній in-process
    # admin-bot thread). telegram-service — типовий, але не єдиний можливий
    # клієнт: будь-який інший сервіс так само може ходити сюди по HTTP.
    rpc_app = create_rpc_app(svc)
    uv_config = uvicorn.Config(
        rpc_app, host=RPC_HOST, port=RPC_PORT, log_level="warning", lifespan="off",
    )
    uv_server = uvicorn.Server(uv_config)
    rpc_task = asyncio.create_task(uv_server.serve(), name="core-rpc")
    log.info(f"RPC server listening on {RPC_HOST}:{RPC_PORT}")

    try:
        while True:
            await asyncio.sleep(30)
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("Shutdown requested...")
        try:
            # Обмежуємо час на очищення ресурсів
            await asyncio.wait_for(scheduler.stop(), timeout=20.0)
        except asyncio.TimeoutError:
            log.warning("Shutdown timed out, forcing exit")

        uv_server.should_exit = True
        try:
            await asyncio.wait_for(rpc_task, timeout=5.0)
        except asyncio.TimeoutError:
            rpc_task.cancel()

        from src.core.account_client import account_client
        from src.core.day_client import day_client
        await account_event_bus.stop()
        await day_event_bus.stop()
        await account_client.aclose()
        await day_client.aclose()
        log.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())