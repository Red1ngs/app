"""
core/rpc/server.py — generic RPC-сервер над `SchedulerService`.

Протокол (навмисно generic, як `POST /accounts/{id}/request` в
account-service, а не 20 окремих ручок):

    POST /rpc/{method}
    Authorization: Bearer <CORE_SERVICE_TOKEN>   (якщо токен заданий)
    body: {"args": [...], "kwargs": {...}}

    200 → {"result": <jsonable>}
    404 → метод відсутній у whitelist (typo / спроба звернутись до
          приватного/несеріалізовного методу — напр. get_bot)
    401 → відсутній/невірний токен
    422 → сервісний метод кинув виняток (текст — у "detail")

Лише методи з `ALLOWED_METHODS` доступні по мережі. Це свідомо явний
allow-list, а не `getattr` навмання — щоб приватні (`_register`,
`_run_on_home_loop`) чи несеріалізовні (`get_bot`) методи `SchedulerService`
не потрапили в мережу через випадковий typo на клієнті.
"""
from __future__ import annotations

import os
from typing import Any, TYPE_CHECKING

from fastapi import Depends, FastAPI, HTTPException, Header
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

if TYPE_CHECKING:
    from src.core.services.scheduler_service import SchedulerService
from src.core.logging.loggers import get_logger

log = get_logger("core.rpc")

CORE_SERVICE_TOKEN = os.environ.get("CORE_SERVICE_TOKEN", "")

# Явний whitelist — публічна мережева поверхня SchedulerService.
ALLOWED_METHODS: frozenset[str] = frozenset({
    "snapshot",
    "account_info",
    "account_ids",
    "connect_account",
    "disconnect_account",
    "register_account",
    "add_account",
    "update_account_data",
    "add_profession",
    "remove_profession",
    "set_professions",
    "remove",
    "force_parse_mangas",
    "mark_mangas_read",
    "update_reading_params",
    "reset_catalog_page",
    "get_reader_state",
    "pause",
    "resume",
    "get_account_error",
    "find_account_by_email",
    "account_status",
    "reconnect_account",
    "problem_accounts",
    "logs_list_accounts",
    "logs_tail_account",
    "logs_tail_scheduler",
    "logs_errors",
    "known_professions",
})


class RpcRequest(BaseModel):
    args:   list[Any] = []
    kwargs: dict[str, Any] = {}


def _check_token(authorization: str | None = Header(default=None)) -> None:
    if not CORE_SERVICE_TOKEN:
        return  # токен не задано — dev-режим, довіряємо docker-мережі
    expected = f"Bearer {CORE_SERVICE_TOKEN}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="invalid or missing token")


def create_rpc_app(service: SchedulerService) -> FastAPI:
    app = FastAPI(title="core-service RPC", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/accounts")
    async def health_accounts() -> dict[str, Any]:
        """
        На відміну від /health (тільки "процес живий"), тут — реальний
        стан акаунтів. НЕ підключено до docker healthcheck навмисно: якщо
        акаунт забанили чи проксі відвалилось, це не привід перезапускати
        весь контейнер (і тим більше не привід для docker-залежних
        сервісів (`depends_on: condition: service_healthy`) вважати
        core-service недоступним) — для цього є problem_accounts()/
        ReconnectWatchdog і алерти в telegram-service. Цей ендпоінт —
        для людини чи зовнішнього моніторингу (напр. Prometheus blackbox,
        uptime-check), яка хоче знати, чи все дійсно ОК, а не тільки
        "uvicorn відповідає".
        """
        total = len(await service.account_ids())
        problems = await service.problem_accounts()
        by_status: dict[str, int] = {}
        for p in problems:
            by_status[p["status"]] = by_status.get(p["status"], 0) + 1
        return {
            "total_accounts": total,
            "problem_accounts": len(problems),
            "by_status": by_status,
            "details": problems,
        }

    @app.post("/rpc/{method}", dependencies=[Depends(_check_token)])
    async def rpc(method: str, payload: RpcRequest) -> dict[str, Any]:
        if method not in ALLOWED_METHODS:
            raise HTTPException(status_code=404, detail=f"unknown method {method!r}")

        fn = getattr(service, method)
        try:
            result = fn(*payload.args, **payload.kwargs)
            if hasattr(result, "__await__"):
                result = await result
        except (TypeError, ValueError) as e:
            raise HTTPException(status_code=422, detail=str(e))
        except Exception as e:
            log.error(f"[rpc] {method} failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

        return {"result": jsonable_encoder(result)}

    return app
