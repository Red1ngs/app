"""
telegram_service/core_client.py

Тонкий async HTTP-клієнт до `core-service` (`src/core/rpc/server.py`).

Клас `CoreServiceClient` навмисно повторює сигнатури публічних методів
`SchedulerService` з `core-service` — це дає змогу переносити роутери
адмін-бота сюди практично без змін: `data["svc"]` як був "об'єктом з
такими-то async-методами", так і лишився, просто тепер виклик іде по
мережі (`POST {CORE_SERVICE_URL}/rpc/{method}`), а не в тому самому
процесі.

DTO (`AccountInfo`, `MangabuffInfo`, `SchedulerSnapshot`) — дзеркальні
копії тих, що описані в `core-service`
(`src/core/services/scheduler_service.py`). Навмисно НЕ imported
звідти: telegram-service — окремий образ з окремим pyproject і не тягне
бізнес-код `src.core` як залежність (те саме розділення, що вже є між
`account-service` і бізнес-застосунком).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

log = logging.getLogger("telegram_service.core_client")


class CoreServiceError(RuntimeError):
    """Мережева помилка або помилка core-service (не бізнес-помилка типу `(False, "...")`)."""


# ─────────────────────────────────────────────────────────────────────────────
# DTO (дзеркало src/core/services/scheduler_service.py)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MangabuffInfo:
    user_name: str
    user_id:   str


@dataclass(frozen=True)
class AccountInfo:
    account_id:   str
    email:        str
    proxy:        str
    status:       str
    mangabuff:    MangabuffInfo
    queue_size:   int = 0
    professions:  list[str] = field(default_factory=list)
    monitors:     list[str] = field(default_factory=list)
    is_connected: bool = False

    @property
    def profession(self) -> Optional[str]:
        return self.professions[0] if self.professions else None


@dataclass(frozen=True)
class SchedulerSnapshot:
    total_accounts: int
    accounts:       list[AccountInfo]


def _account_info_from_dict(d: dict[str, Any]) -> AccountInfo:
    mb = d["mangabuff"]
    return AccountInfo(
        account_id=d["account_id"],
        email=d["email"],
        proxy=d["proxy"],
        status=d["status"],
        mangabuff=MangabuffInfo(user_name=mb["user_name"], user_id=mb["user_id"]),
        queue_size=d.get("queue_size", 0),
        professions=d.get("professions", []),
        monitors=d.get("monitors", []),
        is_connected=d.get("is_connected", False),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Клієнт
# ─────────────────────────────────────────────────────────────────────────────

class CoreServiceClient:
    def __init__(self, base_url: str, token: str = "", timeout: float = 30.0) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _rpc(self, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            resp = await self._http.post(f"/rpc/{method}", json={"args": list(args), "kwargs": kwargs})
        except httpx.HTTPError as e:
            raise CoreServiceError(f"core-service недоступний: {e}") from e

        if resp.status_code == 404:
            raise CoreServiceError(f"core-service: невідомий метод {method!r}")
        if resp.status_code == 401:
            raise CoreServiceError("core-service: невірний CORE_SERVICE_TOKEN")
        if resp.status_code >= 400:
            detail = resp.json().get("detail", resp.text) if resp.content else resp.text
            raise CoreServiceError(f"core-service [{method}]: {detail}")

        return resp.json()["result"]

    # ── Читання стану ─────────────────────────────────────────────────────────

    async def snapshot(self) -> SchedulerSnapshot:
        raw = await self._rpc("snapshot")
        return SchedulerSnapshot(
            total_accounts=raw["total_accounts"],
            accounts=[_account_info_from_dict(a) for a in raw["accounts"]],
        )

    async def account_info(self, account_id: str) -> Optional[AccountInfo]:
        raw = await self._rpc("account_info", account_id)
        return _account_info_from_dict(raw) if raw else None

    async def account_ids(self) -> list[str]:
        return await self._rpc("account_ids")

    async def get_account_error(self, account_id: str) -> Optional[str]:
        return await self._rpc("get_account_error", account_id)

    async def find_account_by_email(self, email: str) -> Optional[str]:
        return await self._rpc("find_account_by_email", email)

    async def connect_account(self, account_id: str) -> bool:
        return await self._rpc("connect_account", account_id)

    async def disconnect_account(self, account_id: str) -> bool:
        return await self._rpc("disconnect_account", account_id)

    # ── Створення / видалення акаунта ───────────────────────────────────────

    async def register_account(self, account_id: str, email: str) -> tuple[bool, str]:
        r = await self._rpc("register_account", account_id, email)
        return tuple(r)  # type: ignore[return-value]

    async def add_account(
        self,
        account_id: str,
        email:      str,
        password:   str = "",
        proxy:      str = "",
    ) -> tuple[bool, str]:
        r = await self._rpc("add_account", account_id, email, password=password, proxy=proxy)
        return tuple(r)  # type: ignore[return-value]

    async def remove(self, account_id: str) -> bool:
        return await self._rpc("remove", account_id)

    # ── Professions ──────────────────────────────────────────────────────────

    async def add_profession(self, account_id: str, profession_name: str, *, priority: int = -1) -> tuple[bool, str]:
        r = await self._rpc("add_profession", account_id, profession_name, priority=priority)
        return tuple(r)  # type: ignore[return-value]

    async def remove_profession(self, account_id: str, profession_name: str) -> tuple[bool, str]:
        r = await self._rpc("remove_profession", account_id, profession_name)
        return tuple(r)  # type: ignore[return-value]

    async def set_professions(self, account_id: str, profession_names: list[str]) -> tuple[bool, str]:
        r = await self._rpc("set_professions", account_id, profession_names)
        return tuple(r)  # type: ignore[return-value]

    async def known_professions(self) -> list[str]:
        return await self._rpc("known_professions")

    # ── Async операції (manga/reader) ───────────────────────────────────────

    async def force_parse_mangas(self, account_id: str, targets: list[str]) -> tuple[bool, str, dict[str, Any]]:
        r = await self._rpc("force_parse_mangas", account_id, targets=targets)
        return tuple(r)  # type: ignore[return-value]

    async def mark_mangas_read(self, account_id: str, targets: list[str]) -> tuple[bool, str, dict[str, Any]]:
        r = await self._rpc("mark_mangas_read", account_id, targets=targets)
        return tuple(r)  # type: ignore[return-value]

    async def update_reading_params(
        self,
        account_id:   str,
        limit:        int = 2,
        include_tags: Optional[list[str]] = None,
        exclude_tags: Optional[list[str]] = None,
    ) -> bool:
        return await self._rpc(
            "update_reading_params", account_id,
            limit=limit, include_tags=include_tags, exclude_tags=exclude_tags,
        )

    async def reset_catalog_page(self, account_id: str) -> tuple[bool, str]:
        r = await self._rpc("reset_catalog_page", account_id)
        return tuple(r)  # type: ignore[return-value]

    async def get_reader_state(self, account_id: str) -> tuple[bool, dict[str, Any]]:
        r = await self._rpc("get_reader_state", account_id)
        return tuple(r)  # type: ignore[return-value]

    async def pause(self, account_id: str) -> bool:
        return await self._rpc("pause", account_id)

    async def resume(self, account_id: str) -> bool:
        return await self._rpc("resume", account_id)

    # ── Логи ─────────────────────────────────────────────────────────────────

    async def logs_list_accounts(self) -> list[str]:
        return await self._rpc("logs_list_accounts")

    async def logs_tail_account(self, account_id: str, n: int = 40) -> list[str]:
        return await self._rpc("logs_tail_account", account_id, n=n)

    async def logs_tail_scheduler(self, n: int = 40) -> list[str]:
        return await self._rpc("logs_tail_scheduler", n=n)

    async def logs_errors(self, since_hours: float = 24) -> list[str]:
        return await self._rpc("logs_errors", since_hours=since_hours)
