"""
reconnect_watchdog.py — фонове відновлення акаунтів, що впали в ERROR.

AccountStatus.ERROR за задумом (src/core/status.py) означає "проблема, бот
намагається відновитись" — але до цього модуля ніхто фактично й не
намагався: StartupManager підключає кожен акаунт лише на старті процесу
(навіть із ретраями — see startup_manager.py), а після цього єдиний спосіб
повторно підключити акаунт, що впав в ERROR (наприклад, тимчасово погане
проксі, рестарт account-service, 401 без вдалого re-login тощо) — ручний
виклик /reconnect з телеграм-бота чи рестарт усього compose-стеку.

ReconnectWatchdog — окремий asyncio-таск у "домашньому" loop'і scheduler'а
(створюється й зупиняється разом з ним у main.py). Раз на `interval` секунд
проходить по всіх акаунтах у статусі ERROR і намагається підняти сесію
знову (SchedulerService.reconnect_account — connect() + довстановлення
професій, якщо вони ще не прикріплені).

Акаунти в DEAD (бан, остаточна помилка) чи SUSPENDED (свідома пауза
адміном) НЕ чіпає — там потрібне зовнішнє втручання, а не ретрай.

Backoff — окремий на кожен акаунт (експоненційний, зі стелею), щоб не
довбитись у мертвий пароль/проксі щохвилини і не заливати логи й
account-service запитами.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from src.core.config.app import ReconnectCfg
from src.core.logging.loggers import get_scheduler_logger

if TYPE_CHECKING:
    from src.core.services.scheduler_service import SchedulerService

log = get_scheduler_logger()

_PROBLEM_STATUS = "ERROR"  # єдиний статус, який цей watchdog намагається лікувати


@dataclass
class _RetryState:
    attempts:          int   = 0
    next_attempt_at:   float = 0.0


class ReconnectWatchdog:
    def __init__(self, service: "SchedulerService", cfg: Optional[ReconnectCfg] = None) -> None:
        self._service = service
        self._cfg = cfg or ReconnectCfg()
        self._retry_state: dict[str, _RetryState] = {}
        self._task: Optional[asyncio.Task[None]] = None
        self._stopping = False

    def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="reconnect-watchdog")
        log.info(
            f"[ReconnectWatchdog] запущено "
            f"(interval={self._cfg.interval}s, base_backoff={self._cfg.base_backoff}s, "
            f"max_backoff={self._cfg.max_backoff}s)"
        )

    async def stop(self) -> None:
        self._stopping = True
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error(f"[ReconnectWatchdog] помилка під час зупинки: {e}", exc_info=True)
        finally:
            self._task = None
        log.info("[ReconnectWatchdog] зупинено")

    async def _run(self) -> None:
        while not self._stopping:
            try:
                await asyncio.sleep(self._cfg.interval)
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Watchdog не повинен вмерти через одну випадкову помилку —
                # інакше акаунти лишаться без відновлення до рестарту процесу.
                log.error(f"[ReconnectWatchdog] помилка в циклі перевірки: {e}", exc_info=True)

    async def _tick(self) -> None:
        loop = asyncio.get_running_loop()
        now = loop.time()

        current_ids = set(await self._service.account_ids())

        # Прибираємо стан для акаунтів, яких більше немає (видалені вручну).
        for stale_id in set(self._retry_state) - current_ids:
            self._retry_state.pop(stale_id, None)

        for account_id in current_ids:
            status = await self._service.account_status(account_id)

            if status != _PROBLEM_STATUS:
                # Відновився сам (IDLE/WORKING/COOLDOWN) або потребує людини
                # (DEAD/SUSPENDED) — знімаємо з відстеження в обох випадках.
                self._retry_state.pop(account_id, None)
                continue

            state = self._retry_state.setdefault(account_id, _RetryState())
            if now < state.next_attempt_at:
                continue

            state.attempts += 1
            backoff = min(
                self._cfg.base_backoff * (self._cfg.backoff_multiplier ** (state.attempts - 1)),
                self._cfg.max_backoff,
            )
            state.next_attempt_at = now + backoff

            log.info(f"[ReconnectWatchdog] [{account_id}] спроба відновлення №{state.attempts} …")
            try:
                ok = await self._service.reconnect_account(account_id)
            except Exception as e:
                log.error(
                    f"[ReconnectWatchdog] [{account_id}] виняток під час відновлення: {e}",
                    exc_info=True,
                )
                continue

            if ok:
                log.info(f"[ReconnectWatchdog] [{account_id}] ✅ відновлено")
                self._retry_state.pop(account_id, None)
            else:
                log.warning(
                    f"[ReconnectWatchdog] [{account_id}] ✗ спроба №{state.attempts} невдала, "
                    f"наступна не раніше ніж через {backoff:.0f}s"
                )
